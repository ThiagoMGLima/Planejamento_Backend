"""Views da API (Handoff §8).

Marco 3 acrescenta: janela de eventos com expansão de ocorrências, transições
concluir/remarcar (com escopo), pendentes e feriados.
"""

from datetime import date, timedelta

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

from .filters import TarefaFilter
from .models import Classe, Evento, Tarefa
from .serializers import (
    AplicarSerializer,
    CalcularSerializer,
    ClasseSerializer,
    EventoSerializer,
    PlanejarSerializer,
    PromoverSerializer,
    TarefaSerializer,
)
from .services import completion, holidays, planejamento
from .services.recurrence import expandir

JANELA_MAX = timedelta(days=92)


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
        resultado = completion.concluir(evento, escopo=escopo, data=data)
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

    ids = list(dict.fromkeys(dados["tarefa_ids"]))  # dedup preservando ordem
    por_id = {
        t.id: t for t in Tarefa.objects.select_related("classe").filter(id__in=ids)
    }

    invalidas = []
    validas = []
    for tid in ids:
        tarefa = por_id.get(tid)
        if tarefa is None:
            invalidas.append({"tarefa_id": str(tid), "motivo": "tarefa inexistente"})
            continue
        if tarefa.status == Tarefa.Status.PROMOVIDA:
            invalidas.append({"tarefa_id": str(tid), "motivo": "tarefa já promovida"})
            continue
        faltando = []
        if tarefa.deadline is None:
            faltando.append("deadline")
        if not tarefa.esforco_estimado:
            faltando.append("esforco_estimado")
        if tarefa.classe_id is None:
            faltando.append("classe")
        if faltando:
            invalidas.append(
                {"tarefa_id": str(tid), "motivo": f"faltando: {', '.join(faltando)}"}
            )
            continue
        validas.append(tarefa)

    if invalidas:
        return Response(
            {"tarefas_invalidas": invalidas},
            status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    prefs, prefs_usadas = planejamento.montar_preferencias(
        dados.get("preferencias", {})
    )
    agora = dados.get("a_partir_de") or timezone.now()
    horizonte_fim = min(max(t.deadline for t in validas), agora + JANELA_MAX)

    tarefas = [
        planejamento.TarefaEntrada(
            id=str(t.id),
            titulo=t.titulo,
            classe_id=str(t.classe_id),
            esforco=t.esforco_estimado,
            deadline=t.deadline,
        )
        for t in validas
    ]
    ocupado = planejamento.intervalos_ocupados(agora, horizonte_fim)
    sessoes, nao_alocado = planejamento.calcular_plano(
        tarefas, ocupado, prefs, agora, horizonte_fim
    )

    return Response(
        {
            "sessoes": [
                {
                    "tarefa_id": s.tarefa_id,
                    "tarefa_titulo": s.tarefa_titulo,
                    "classe_id": s.classe_id,
                    "inicio": s.inicio.isoformat(),
                    "fim": s.fim.isoformat(),
                    "dur_min": s.dur_min,
                }
                for s in sessoes
            ],
            "nao_alocado": [vars(n) for n in nao_alocado],
            "preferencias_usadas": prefs_usadas,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def planejamento_aplicar(request):
    """POST /planejamento/aplicar → cria os eventos das sessões revisadas.

    Generaliza /tarefas/{id}/planejar para várias tarefas: agrupa por tarefa_id,
    cria um Evento por sessão (atômico) e marca as tarefas como PROMOVIDA.
    """
    entrada = AplicarSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    sessoes = entrada.validated_data["sessoes"]

    ids = {s["tarefa_id"] for s in sessoes}
    por_id = {
        t.id: t for t in Tarefa.objects.select_related("classe").filter(id__in=ids)
    }

    faltando = [str(tid) for tid in ids if tid not in por_id]
    if faltando:
        return Response(
            {"tarefa_id": [f"Tarefa(s) inexistente(s): {', '.join(faltando)}"]},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    sem_classe = [str(tid) for tid in ids if por_id[tid].classe_id is None]
    if sem_classe:
        return Response(
            {"classe_id": [f"Tarefa(s) sem classe: {', '.join(sem_classe)}"]},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        criados = [
            Evento.objects.create(
                titulo=por_id[s["tarefa_id"]].titulo,
                descricao=por_id[s["tarefa_id"]].descricao,
                inicio=s["inicio"],
                fim=s["fim"],
                classe=por_id[s["tarefa_id"]].classe,
                rastrear_conclusao=True,
                status=Evento.Status.AGENDADO,
                origem_tarefa=por_id[s["tarefa_id"]],
            )
            for s in sessoes
        ]
        for tarefa in por_id.values():
            if tarefa.status != Tarefa.Status.PROMOVIDA:
                tarefa.status = Tarefa.Status.PROMOVIDA
                tarefa.save(update_fields=["status", "atualizado_em"])

    return Response(
        EventoSerializer(criados, many=True).data,
        status=http_status.HTTP_201_CREATED,
    )
