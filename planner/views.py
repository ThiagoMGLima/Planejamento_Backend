"""Views da API (Handoff §8).

Marco 3 acrescenta: janela de eventos com expansão de ocorrências, transições
concluir/remarcar (com escopo), pendentes e feriados.
"""

from datetime import date, timedelta
from uuid import uuid4

from celery.result import AsyncResult
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import tasks
from .filters import TarefaFilter
from .models import Classe, EscolhaCenario, Evento, Tarefa
from .serializers import (
    AgenteChatSerializer,
    AplicarSerializer,
    CalcularSerializer,
    ClasseSerializer,
    EscolherCenarioSerializer,
    EventoSerializer,
    PlanejarSerializer,
    PromoverSerializer,
    RefinarCenarioSerializer,
    ReplanejarSerializer,
    TarefaSerializer,
)
from .services import (
    adaptacao,
    aplicacao,
    completion,
    holidays,
    planejamento,
    planejamento_ia,
    replanejamento,
    tempos,
)
from .services.planejamento import HORIZONTES, JANELA_MAX
from .services.recurrence import expandir


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """GET /api/v1/health → 200. Usado pelo healthcheck do Docker."""
    return Response({"status": "ok"})


class ClasseViewSet(viewsets.ModelViewSet):
    queryset = Classe.objects.all()
    serializer_class = ClasseSerializer

    def destroy(self, request, *args, **kwargs):
        # DELETE bloqueado (409) se houver eventos na classe (on_delete=PROTECT).
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "detail": "Classe em uso por um ou mais eventos; "
                    "reatribua-os antes de apagar."
                },
                status=http_status.HTTP_409_CONFLICT,
            )


class TarefaViewSet(viewsets.ModelViewSet):
    queryset = Tarefa.objects.select_related("classe").all()
    serializer_class = TarefaSerializer
    filterset_class = TarefaFilter

    @action(detail=True, methods=["post"])
    def promover(self, request, pk=None):
        """Arrasto Inbox → calendário (Handoff §8.2)."""
        tarefa = self.get_object()
        entrada = PromoverSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        classe = dados.get("classe") or tarefa.classe
        if classe is None:
            return Response(
                {"classe_id": ["Tarefa sem classe; informe classe_id."]},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        inicio = dados["inicio"]
        fim = dados.get("fim")
        if fim is None:
            if tarefa.esforco_estimado:
                fim = inicio + timedelta(minutes=tarefa.esforco_estimado)
            else:
                fim = inicio + timedelta(hours=1)

        with transaction.atomic():
            evento = Evento.objects.create(
                titulo=tarefa.titulo,
                descricao=tarefa.descricao,
                inicio=inicio,
                fim=fim,
                classe=classe,
                # Default: todo evento acompanha conclusão (independe da classe).
                rastrear_conclusao=True,
                status=Evento.Status.AGENDADO,
                origem_tarefa=tarefa,
            )
            tarefa.status = Tarefa.Status.PROMOVIDA
            tarefa.save(update_fields=["status", "atualizado_em"])

        return Response(
            EventoSerializer(evento).data, status=http_status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def planejar(self, request, pk=None):
        """Divide a produção de um To Do em N eventos-sessão.

        Recebe a divisão final (sugerida pelo app, ajustada pelo usuário) e cria
        um Evento por sessão, todos vinculados à tarefa (origem_tarefa). A soma
        das sessões é o tempo de produção; cada uma acompanha conclusão.
        """
        tarefa = self.get_object()
        entrada = PlanejarSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        classe = dados.get("classe") or tarefa.classe
        if classe is None:
            return Response(
                {"classe_id": ["Tarefa sem classe; informe classe_id."]},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            eventos = [
                Evento.objects.create(
                    titulo=tarefa.titulo,
                    descricao=tarefa.descricao,
                    inicio=s["inicio"],
                    fim=s["fim"],
                    classe=classe,
                    rastrear_conclusao=True,
                    status=Evento.Status.AGENDADO,
                    origem_tarefa=tarefa,
                )
                for s in dados["sessoes"]
            ]
            tarefa.status = Tarefa.Status.PROMOVIDA
            tarefa.save(update_fields=["status", "atualizado_em"])

        return Response(
            EventoSerializer(eventos, many=True).data,
            status=http_status.HTTP_201_CREATED,
        )


class EventoViewSet(viewsets.ModelViewSet):
    queryset = Evento.objects.select_related(
        "classe", "regra_recorrencia", "origem_tarefa"
    ).all()
    serializer_class = EventoSerializer

    def list(self, request, *args, **kwargs):
        """GET /eventos?inicio&fim → ocorrências expandidas (Handoff §8.3).

        inicio/fim são obrigatórios; recusa janela aberta, naive ou > ~92 dias.
        """
        inicio = self._parse_janela(request.query_params.get("inicio"), "inicio")
        fim = self._parse_janela(request.query_params.get("fim"), "fim")
        if fim <= inicio:
            raise ValidationError({"fim": "fim deve ser maior que inicio."})
        if fim - inicio > JANELA_MAX:
            raise ValidationError({"detail": "Janela máxima de ~92 dias."})

        feriados = set()
        for ano in range(inicio.year, fim.year + 1):
            feriados |= holidays.feriados_do_ano(ano)

        itens = []
        # Eventos não recorrentes que cruzam a janela.
        simples = Evento.objects.filter(
            regra_recorrencia__isnull=True, inicio__lt=fim, fim__gt=inicio
        ).select_related("classe", "origem_tarefa")
        for ev in simples:
            payload = EventoSerializer(ev).data
            payload["ocorrencia"] = None
            itens.append(payload)

        # Eventos recorrentes: expande dentro da janela.
        recorrentes = (
            Evento.objects.filter(regra_recorrencia__isnull=False)
            .select_related("classe", "regra_recorrencia", "origem_tarefa")
            .prefetch_related("ocorrencias")
        )
        for ev in recorrentes:
            for view in expandir(ev, inicio, fim, feriados):
                itens.append(self._payload_ocorrencia(ev, view))

        itens.sort(key=lambda x: x["inicio"])
        return Response(itens)

    @staticmethod
    def _parse_janela(valor, campo):
        if not valor:
            raise ValidationError({campo: "Parâmetro obrigatório."})
        dt = parse_datetime(valor)
        if dt is None:
            raise ValidationError({campo: "Inválido (use ISO-8601 com offset)."})
        if timezone.is_naive(dt):
            raise ValidationError({campo: "Datas devem ser tz-aware (com offset)."})
        return dt

    @staticmethod
    def _payload_ocorrencia(evento, view):
        payload = EventoSerializer(evento).data
        payload["inicio"] = view.inicio.isoformat()
        payload["fim"] = view.fim.isoformat()
        payload["status"] = view.status
        payload["status_efetivo"] = completion.status_efetivo(view)
        payload["ocorrencia"] = {
            "data": view.data.isoformat(),
            "persistida": view.persistida,
        }
        return payload

    @action(detail=True, methods=["post"])
    def concluir(self, request, pk=None):
        evento = self.get_object()
        escopo, data = self._parse_escopo(request, evento)
        real_min = self._parse_real_min(request)
        resultado = completion.concluir(
            evento, escopo=escopo, data=data, real_min=real_min
        )
        if escopo == "serie":
            return Response(EventoSerializer(evento).data)
        return Response(
            {
                "evento": str(evento.id),
                "data": data.isoformat(),
                "status_override": resultado.status_override,
            }
        )

    @action(detail=True, methods=["post"])
    def remarcar(self, request, pk=None):
        evento = self.get_object()
        escopo, data = self._parse_escopo(request, evento)
        resultado, tarefa = completion.remarcar(evento, escopo=escopo, data=data)
        body = {"tarefa_reaberta": TarefaSerializer(tarefa).data}
        if escopo == "serie":
            body["evento"] = EventoSerializer(evento).data
        else:
            body["evento"] = str(evento.id)
            body["data"] = data.isoformat()
            body["status_override"] = resultado.status_override
        return Response(body)

    @staticmethod
    def _parse_real_min(request):
        """`{"real_min": 90}` opcional no concluir (Marco C3) — retrocompatível."""
        valor = request.data.get("real_min")
        if valor is None:
            return None
        try:
            valor = int(valor)
        except (TypeError, ValueError):
            raise ValidationError({"real_min": "Deve ser um inteiro ≥ 1."})
        if valor < 1:
            raise ValidationError({"real_min": "Deve ser um inteiro ≥ 1."})
        return valor

    @staticmethod
    def _parse_escopo(request, evento):
        """Resolve ?escopo=ocorrencia|serie (Handoff §8.3).

        Não recorrente → sempre 'serie'. 'ocorrencia' exige ?data=YYYY-MM-DD.
        """
        escopo = request.query_params.get("escopo")
        if evento.regra_recorrencia is None:
            return "serie", None
        escopo = escopo or "ocorrencia"
        if escopo not in ("ocorrencia", "serie"):
            raise ValidationError({"escopo": "Use 'ocorrencia' ou 'serie'."})
        if escopo == "serie":
            return "serie", None
        data_str = request.query_params.get("data")
        if not data_str:
            raise ValidationError(
                {"data": "Obrigatório para escopo=ocorrencia (YYYY-MM-DD)."}
            )
        try:
            data = date.fromisoformat(data_str)
        except ValueError:
            raise ValidationError({"data": "Formato inválido (use YYYY-MM-DD)."})
        return "ocorrencia", data


@api_view(["GET"])
@permission_classes([AllowAny])
def pendentes(request):
    """GET /pendentes → eventos rastreáveis com status_efetivo == PENDENTE.

    Ordem por `fim` asc (Handoff §8.4). PENDENTE é calculado, nunca gravado:
    filtramos pelas condições que o derivam (rastreável, ainda AGENDADO,
    agora > fim). Cobre eventos não recorrentes; ocorrências recorrentes
    pendentes dependem de janela (ver GET /eventos).
    """
    agora = timezone.now()
    qs = (
        Evento.objects.filter(
            regra_recorrencia__isnull=True,
            rastrear_conclusao=True,
            status=Evento.Status.AGENDADO,
            fim__lt=agora,
        )
        .select_related("classe", "origem_tarefa")
        .order_by("fim")
    )
    return Response(EventoSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def feriados(request):
    """GET /feriados?ano=2026 → feriados nacionais (Handoff §7/§8.4)."""
    ano = request.query_params.get("ano")
    if not ano:
        raise ValidationError({"ano": "Parâmetro obrigatório."})
    try:
        ano = int(ano)
    except ValueError:
        raise ValidationError({"ano": "Deve ser um inteiro."})
    datas = sorted(holidays.feriados_do_ano(ano))
    return Response({"ano": ano, "feriados": [d.isoformat() for d in datas]})


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_calcular(request):
    """POST /planejamento/calcular → plano multitarefa (preview, não persiste).

    Calcula sessões para todas as tarefas selecionadas de uma vez, sem conflito
    com eventos existentes nem entre si. Tarefa inválida (inexistente, já
    promovida ou sem deadline/esforço/classe) → 422 com a lista.
    """
    entrada = CalcularSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    validas, invalidas = planejamento.validar_tarefas(dados["tarefa_ids"])
    if invalidas:
        return Response(
            {"tarefas_invalidas": invalidas},
            status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    agora = dados.get("a_partir_de") or timezone.now()
    res = planejamento.montar_plano(validas, agora, dados.get("preferencias", {}))
    return Response(planejamento.serializar_plano(res))


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_planejar_ia(request):
    """POST /planejamento/planejar-ia → plano melhorado pela IA (assíncrono).

    Body igual ao /calcular. Valida 400/422 de forma síncrona; se o resultado já
    estiver em cache, devolve 200 pronto; senão enfileira a task e devolve 202
    com o job_id (front faz polling no endpoint de status).
    """
    entrada = CalcularSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    validas, invalidas = planejamento.validar_tarefas(dados["tarefa_ids"])
    if invalidas:
        return Response(
            {"tarefas_invalidas": invalidas},
            status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    agora = dados.get("a_partir_de") or timezone.now()
    prefs_entrada = dados.get("preferencias", {})
    horizonte_dias = HORIZONTES[dados["horizonte"]]
    base = planejamento.montar_plano(
        validas, agora, prefs_entrada, horizonte_dias=horizonte_dias
    )
    plano_base = planejamento.serializar_plano(base)

    ids = [str(t.id) for t in validas]
    chave = tasks._chave_cache(
        ids, plano_base["preferencias_usadas"], plano_base["sessoes"]
    )
    hit = cache.get(chave)
    if hit is not None:
        return Response({"status": "pronto", "resultado": hit})

    job = tasks.planejar_ia_task.delay(
        ids, agora.isoformat(), prefs_entrada, horizonte_dias
    )
    return Response(
        {
            "job_id": job.id,
            "status": "processando",
            "tempo_estimado_s": tempos.estimar(
                "planejar_ia", planejamento_ia.estimar_tempo_s(base)
            ),
        },
        status=http_status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def planejamento_estimativa(request):
    """GET /planejamento/planejar-ia/estimativa → tempo previsto, sem chamar a IA.

    Query: `tarefa_ids` (repetido) e `horizonte` (default AUTOMATICO). Monta só o
    plano base (solver, barato) para o horizonte escolhido e devolve quantas
    tarefas entram no escopo + o tempo estimado — para o front avisar a espera
    antes de o usuário disparar a geração. Tarefas inválidas são ignoradas.
    """
    horizonte = request.query_params.get("horizonte", "AUTOMATICO")
    if horizonte not in HORIZONTES:
        return Response(
            {"horizonte": f"valor inválido; use um de {list(HORIZONTES)}."},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    ids = request.query_params.getlist("tarefa_ids")
    validas, _ = planejamento.validar_tarefas(ids)
    if not validas:
        return Response(
            {
                "n_tarefas_no_escopo": 0,
                "tempo_estimado_s": tempos.estimar(
                    "planejar_ia", settings.PLANEJAR_TEMPO_BASE_S
                ),
            }
        )

    agora = timezone.now()
    base = planejamento.montar_plano(
        validas, agora, {}, horizonte_dias=HORIZONTES[horizonte]
    )
    return Response(
        {
            "n_tarefas_no_escopo": len({s.tarefa_id for s in base.sessoes}),
            "tempo_estimado_s": tempos.estimar(
                "planejar_ia", planejamento_ia.estimar_tempo_s(base)
            ),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def planejamento_planejar_ia_status(request, job_id):
    """GET /planejamento/planejar-ia/{job_id} → estado do job (AsyncResult)."""
    resultado = AsyncResult(str(job_id))
    if resultado.successful():
        return Response({"status": "pronto", "resultado": resultado.result})
    if resultado.failed():
        return Response({"status": "erro", "detalhe": "falha no processamento"})
    return Response({"status": "processando"})


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_aplicar(request):
    """POST /planejamento/aplicar → cria os eventos das sessões revisadas.

    Generaliza /tarefas/{id}/planejar para várias tarefas: agrupa por tarefa_id,
    cria um Evento por sessão (atômico) e marca as tarefas como PROMOVIDA.
    """
    entrada = AplicarSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)

    try:
        criados = aplicacao.aplicar_sessoes(entrada.validated_data["sessoes"])
    except aplicacao.AplicacaoInvalida as e:
        return Response(e.erros, status=http_status.HTTP_400_BAD_REQUEST)

    return Response(
        EventoSerializer(criados, many=True).data,
        status=http_status.HTTP_201_CREATED,
    )


# --------------------------------------------------------------------------- #
# Cenários com trade-offs (Marco C1b)                                          #
# --------------------------------------------------------------------------- #
@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_cenarios(request):
    """POST /planejamento/cenarios → 3–4 cenários comparáveis (assíncrono).

    Body igual ao /planejar-ia. Valida síncrono; cache hit devolve 200 pronto;
    senão enfileira `gerar_cenarios_task` e devolve 202 com job_id (polling).
    """
    entrada = CalcularSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    validas, invalidas = planejamento.validar_tarefas(dados["tarefa_ids"])
    if invalidas:
        return Response(
            {"tarefas_invalidas": invalidas},
            status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    agora = dados.get("a_partir_de") or timezone.now()
    prefs_entrada = dados.get("preferencias", {})
    horizonte_dias = HORIZONTES[dados["horizonte"]]
    base = planejamento.montar_plano(
        validas, agora, prefs_entrada, horizonte_dias=horizonte_dias
    )
    plano_base = planejamento.serializar_plano(base)

    ids = [str(t.id) for t in validas]
    chave = tasks._chave_cache(
        ids, plano_base["preferencias_usadas"], plano_base["sessoes"], "cenarios"
    )
    hit = cache.get(chave)
    if hit is not None:
        # Cache-hit também ganha um job_id (chave por job): sem ele o lote não
        # seria endereçável pelo escolher nem pelo refinar (C5).
        job_id = str(uuid4())
        cache.set(f"cenarios_job:{job_id}", hit, timeout=3600)
        return Response({"status": "pronto", "job_id": job_id, "resultado": hit})

    job = tasks.gerar_cenarios_task.delay(
        ids, agora.isoformat(), prefs_entrada, horizonte_dias
    )
    return Response(
        {
            "job_id": job.id,
            "status": "processando",
            # A saída tem 3–6 cenários (muito mais tokens que o planejar-ia):
            # semente = FATOR_CENARIOS × fórmula; depois a razão aprendida
            # com as durações reais assume (ver services/tempos.py).
            "tempo_estimado_s": tempos.estimar(
                "cenarios",
                tempos.FATOR_CENARIOS * planejamento_ia.estimar_tempo_s(base),
            ),
        },
        status=http_status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def planejamento_cenarios_status(request, job_id):
    """GET /planejamento/cenarios/{job_id} → estado do job (AsyncResult)."""
    hit = cache.get(f"cenarios_job:{job_id}")
    if hit is not None:
        return Response({"status": "pronto", "resultado": hit})
    resultado = AsyncResult(str(job_id))
    if resultado.successful():
        return Response({"status": "pronto", "resultado": resultado.result})
    if resultado.failed():
        return Response({"status": "erro", "detalhe": "falha no processamento"})
    return Response({"status": "processando"})


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_cenarios_escolher(request):
    """POST /planejamento/cenarios/escolher → grava a escolha e aprende.

    Grava a escolha CRUA (EscolhaCenario, com o lote e os pesos do momento),
    atualiza os pesos (EWMA) e, com aplicar=true, persiste o plano do cenário
    reusando o serviço do /aplicar (transação).
    """
    entrada = EscolherCenarioSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    resultado = cache.get(f"cenarios_job:{dados['job_id']}")
    if resultado is None:
        job = AsyncResult(str(dados["job_id"]))
        resultado = job.result if job.successful() else None
    if not resultado or "cenarios" not in resultado:
        return Response(
            {"job_id": ["Job desconhecido, não finalizado ou expirado."]},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    cenario = next(
        (c for c in resultado["cenarios"] if c["id"] == dados["cenario_id"]), None
    )
    if cenario is None:
        return Response(
            {"cenario_id": ["Cenário inexistente neste lote."]},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    try:
        # Tudo-ou-nada: se o aplicar falhar, nem a escolha nem os pesos ficam.
        with transaction.atomic():
            escolha = EscolhaCenario.objects.create(
                lote=[
                    {
                        "id": c["id"],
                        "nome": c["nome"],
                        "sugerido": c["sugerido"],
                        "score": c["score"],
                        "metricas": c["metricas"],
                        "metricas_vs_base": c["metricas_vs_base"],
                    }
                    for c in resultado["cenarios"]
                ],
                escolhido=cenario["id"],
                era_sugerido=cenario["sugerido"],
                pesos_no_momento=resultado["pesos_usados"],
            )
            pesos = adaptacao.atualizar_pesos(escolha)

            corpo = {"aplicado": False, "pesos": pesos}
            if dados["aplicar"]:
                criados = aplicacao.aplicar_sessoes(cenario["plano"]["sessoes"])
                corpo = {
                    "aplicado": True,
                    "eventos_criados": len(criados),
                    "pesos": pesos,
                }
    except aplicacao.AplicacaoInvalida as e:
        return Response(e.erros, status=http_status.HTTP_400_BAD_REQUEST)

    return Response(corpo)


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_cenarios_refinar(request):
    """POST /planejamento/cenarios/refinar → conversa sobre o lote (Marco C5).

    "Gostei do B, mas sem academia essa semana": a IA traduz o pedido em
    diretrizes, o solver re-roda e o cenário novo entra no MESMO lote (o
    `escolher` segue valendo pelo job_id original). Assíncrono como o gerar:
    202 + polling em GET /planejamento/cenarios/refinar/{refino_id}.
    """
    entrada = RefinarCenarioSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    resultado = cache.get(f"cenarios_job:{dados['job_id']}")
    if resultado is None:
        job = AsyncResult(str(dados["job_id"]))
        resultado = job.result if job.successful() else None
    if not resultado or "cenarios" not in resultado:
        return Response(
            {"job_id": ["Job desconhecido, não finalizado ou expirado."]},
            status=http_status.HTTP_404_NOT_FOUND,
        )
    if not resultado.get("entrada"):
        return Response(
            {
                "job_id": [
                    "Lote antigo, sem dados de entrada; gere os cenários de novo."
                ]
            },
            status=http_status.HTTP_409_CONFLICT,
        )

    cenario_id = dados.get("cenario_id")
    ids = {c["id"] for c in resultado["cenarios"]}
    if cenario_id is not None and cenario_id not in ids:
        return Response(
            {"cenario_id": ["Cenário inexistente neste lote."]},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    job = tasks.refinar_cenario_task.delay(
        dados["job_id"], cenario_id, dados["mensagem"]
    )
    # 1 chamada de IA + solver: mesma ordem de grandeza do gerar; o nº de
    # tarefas sai do plano base do lote (nada de re-rodar o solver aqui).
    base = next(c for c in resultado["cenarios"] if c["id"] == "base")
    n = len({s["tarefa_id"] for s in base["plano"]["sessoes"]})
    return Response(
        {
            "job_id": job.id,
            "status": "processando",
            "tempo_estimado_s": tempos.estimar(
                "refino",
                settings.PLANEJAR_TEMPO_BASE_S
                + settings.PLANEJAR_TEMPO_POR_TAREFA_S * n,
            ),
        },
        status=http_status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def planejamento_cenarios_refinar_status(request, job_id):
    """GET /planejamento/cenarios/refinar/{job_id} → estado do refino."""
    hit = cache.get(f"cenarios_refino:{job_id}")
    if hit is not None:
        return Response({"status": "pronto", "resultado": hit})
    resultado = AsyncResult(str(job_id))
    if resultado.successful():
        return Response({"status": "pronto", "resultado": resultado.result})
    if resultado.failed():
        return Response({"status": "erro", "detalhe": "falha no processamento"})
    return Response({"status": "processando"})


# --------------------------------------------------------------------------- #
# Agente conversacional (Marco C4, o cérebro) — tool-use assíncrono            #
# --------------------------------------------------------------------------- #
@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_agente_chat(request):
    """POST /planejamento/agente/chat → um turno do assistente de rotina.

    Enfileira o loop de tool-use (o LLM decide/executa ferramentas) e devolve
    202 + job_id para polling — mesmo padrão dos cenários, porque uma volta do
    modelo leva dezenas de segundos.
    """
    entrada = AgenteChatSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data
    job = tasks.agente_chat_task.delay(
        dados["conversa_id"], dados["mensagem"], dados.get("contexto") or {}
    )
    return Response(
        {
            "job_id": job.id,
            "status": "processando",
            "tempo_estimado_s": settings.PLANEJAR_TEMPO_BASE_S,
        },
        status=http_status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def planejamento_agente_chat_status(request, job_id):
    """GET /planejamento/agente/chat/{job_id} → estado do turno."""
    hit = cache.get(f"agente_chat:{job_id}")
    if hit is not None:
        return Response({"status": "pronto", "resultado": hit})
    resultado = AsyncResult(str(job_id))
    if resultado.successful():
        return Response({"status": "pronto", "resultado": resultado.result})
    if resultado.failed():
        return Response({"status": "erro", "detalhe": "falha no processamento"})
    return Response({"status": "processando"})


# --------------------------------------------------------------------------- #
# Replanejar a partir de agora (Marco C2) — sem IA, síncrono                   #
# --------------------------------------------------------------------------- #
@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_replanejar(request):
    """POST /planejamento/replanejar → plano novo + diff (nada persistido).

    Congela o passado e re-roda o solver do `agora` em diante, devolvendo o
    esforço das sessões futuras ao pool. "Hoje não" = dias_bloqueados=[hoje].
    """
    entrada = ReplanejarSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    rp = replanejamento.replanejar(
        agora=dados.get("a_partir_de") or timezone.now(),
        dias_bloqueados=dados.get("dias_bloqueados"),
        preferencias=dados.get("preferencias", {}),
    )
    return Response(
        {
            "plano": planejamento.serializar_plano(rp.res),
            "diff": rp.diff,
            "metricas": rp.metricas,
            "metricas_vs_anterior": rp.metricas_vs_anterior,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_replanejar_aplicar(request):
    """POST /planejamento/replanejar/aplicar → recalcula E persiste (transação).

    Recalcular aqui dentro (em vez de confiar num plano enviado pelo cliente)
    evita aplicar plano obsoleto; o corpo é o mesmo da simulação.
    """
    entrada = ReplanejarSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    dados = entrada.validated_data

    try:
        rp, criados, removidos = replanejamento.aplicar_replanejamento(
            agora=dados.get("a_partir_de") or timezone.now(),
            dias_bloqueados=dados.get("dias_bloqueados"),
            preferencias=dados.get("preferencias", {}),
        )
    except aplicacao.AplicacaoInvalida as e:
        return Response(e.erros, status=http_status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "diff": rp.diff,
            "eventos_criados": criados,
            "eventos_removidos": removidos,
            "metricas": rp.metricas,
            "metricas_vs_anterior": rp.metricas_vs_anterior,
        }
    )
