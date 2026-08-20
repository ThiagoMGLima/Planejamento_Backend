"""Importa o planejamento de ensino de uma disciplina (Fase 1.2 / PR B).

Lê um JSON por disciplina e produz, no perfil do dono: a **série recorrente** da
aula (um ou mais eventos) e uma **`Ocorrencia` por data** — o conteúdo daquele
dia, o dia de prova, o dia de entrega, o dia sem aula.

Existe porque as 3 disciplinas cujo PDF foi lançado à mão viraram 58 eventos
avulsos + 9 provas, e os PDFs das outras 4 ainda vêm: se importar continuar sendo
trabalho manual, o erro se repete. Plano em
`docs/tasks/fase1-2-prb-importador.md`.

**Quem decide QUANDO a aula acontece é a série, não este arquivo.** A regra de
recorrência mais `ignorar_feriados` e `data_fim` definem os dias; o JSON só
acrescenta o CONTEÚDO de cada data. Uma data que a regra gera e o arquivo não
lista **continua sendo aula** — entra no relatório como "sem conteúdo
transcrito", nunca some sozinha. Só `sem_aula`, declarado no arquivo, pula um dia.

`ignorar_feriados` vem do JSON, por disciplina, e o default é `true` (igual às
séries lançadas à mão). A disciplina que realmente tem aula em feriado declara
`false` — e aí a aula aparece no feriado.

**Não cria `Tarefa`** (§3.4 do plano-pai): o esforço já mora nas tarefas, e
misturar as duas escritas faria deste comando o lugar da próxima duplicata. O que
ele faz é *relatar* prova/entrega sem tarefa correspondente.

Uso:
    # simulação — não grava nada, mostra o que faria
    python manage.py importar_planejamento_ensino <arquivo.json>

    # gravar
    python manage.py importar_planejamento_ensino <arquivo.json> --aplicar

    # trocar os eventos avulsos da disciplina pela série
    python manage.py importar_planejamento_ensino <arquivo.json> --aplicar --substituir
"""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from planner.models import Classe, Evento, Ocorrencia, Perfil, RegraRecorrencia
from planner.services import perfis
from planner.services.agenda import feriados_da_janela
from planner.services.recurrence import datas_da_regra

DIAS_NOME = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

# Classes que o `--substituir` pode apagar. Um avulso de "Estudar" é bloco
# promovido de tarefa e NUNCA é lançamento de planejamento de ensino.
CLASSES_SUBSTITUIVEIS = {"Aula", "Prova"}

# Classe que um dia de prova assume. A prova chama atenção pela COR da classe —
# é a regra que o frontend já declara (`EventBlock.jsx`), não um tratamento novo.
CLASSE_PROVA = "Prova"


class _Simulacao(Exception):
    """Sentinela para desfazer a transação no fim de um dry-run.

    O dry-run roda a importação inteira e só então desfaz. É o que o torna
    honesto: a validação das datas (§2.5 do plano) exige a série existindo para
    ser expandida, então um dry-run que não escrevesse não teria o que validar.
    """


class Command(BaseCommand):
    help = "Importa o planejamento de ensino de uma disciplina a partir de um JSON."

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="Caminho do JSON da disciplina.")
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava. Sem isto o comando só SIMULA e não toca no banco.",
        )
        parser.add_argument(
            "--substituir",
            action="store_true",
            help=(
                "Apaga os eventos avulsos da disciplina no período antes de criar "
                "a série. Recusa se algum tiver estado do usuário."
            ),
        )
        parser.add_argument(
            "--dono",
            default=None,
            help="E-mail do perfil. Ausente: o perfil local.",
        )

    # ------------------------------------------------------------------ #
    # Entrada                                                            #
    # ------------------------------------------------------------------ #
    def handle(self, *args, **options):
        spec = self._ler(options["arquivo"])
        dono = self._resolver_dono(options["dono"])
        classe = self._resolver_classe(dono, spec["disciplina"]["classe"])

        try:
            with transaction.atomic():
                relatorio = self._importar(
                    spec, dono, classe, substituir=options["substituir"]
                )
                self._imprimir(relatorio, spec)
                if not options["aplicar"]:
                    raise _Simulacao
        except _Simulacao:
            self.stdout.write(
                self.style.WARNING(
                    "\nSIMULAÇÃO — nada foi gravado. Repita com --aplicar."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("\nImportação aplicada."))

    def _ler(self, caminho):
        p = Path(caminho)
        if not p.exists():
            raise CommandError(f"Arquivo não encontrado: {caminho}")
        try:
            spec = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise CommandError(f"JSON inválido em {caminho}: {e}") from e
        validar_spec(spec)
        return spec

    def _resolver_dono(self, email):
        if email is None:
            return perfis.perfil_local()
        try:
            return Perfil.objects.get(email=email)
        except Perfil.DoesNotExist as e:
            raise CommandError(f"Perfil não encontrado: {email}") from e

    def _resolver_classe(self, dono, nome):
        try:
            return Classe.objects.do_dono(dono).get(nome=nome)
        except Classe.DoesNotExist as e:
            raise CommandError(
                f"Classe {nome!r} não existe em {dono}. "
                "Crie-a antes, ou ajuste o JSON."
            ) from e

    # ------------------------------------------------------------------ #
    # Importação                                                         #
    # ------------------------------------------------------------------ #
    def _importar(self, spec, dono, classe, substituir):
        disc = spec["disciplina"]
        data_inicio = date.fromisoformat(spec["data_inicio"])
        data_fim = date.fromisoformat(spec["data_fim"])
        titulo = f"{disc['nome']} ({disc['codigo']})"

        rel = {
            "avulsos_apagados": 0,
            "series": [],
            "conteudo": 0,
            "provas": 0,
            "entregas": 0,
            "sem_conteudo": [],
            "sem_aula": 0,
            "removidas": 0,
            "avisos": [],
        }

        if substituir:
            rel["avulsos_apagados"] = self._apagar_avulsos(
                dono, spec, data_inicio, data_fim, rel
            )

        # --- séries -------------------------------------------------------
        eventos_por_dia = {}
        for rec in spec["recorrencias"]:
            evento, criado = self._upsert_serie(
                dono, classe, titulo, disc, spec, rec, data_inicio, data_fim
            )
            rel["series"].append((evento.titulo, sorted(rec["dias"]), criado))
            for d in rec["dias"]:
                eventos_por_dia[d] = evento

        # --- datas geradas por cada série ---------------------------------
        feriados = feriados_da_janela(
            _local(data_inicio, "00:00"), _local(data_fim, "23:59"), dono
        )
        geradas = {}  # data -> Evento
        for evento in dict.fromkeys(eventos_por_dia.values()):
            for d in self._datas_geradas(evento, data_inicio, data_fim, feriados):
                geradas[d] = evento

        # --- conteúdo do JSON ---------------------------------------------
        do_json = {}
        for item in spec["datas"]:
            d = date.fromisoformat(item["data"])
            if d not in geradas:
                if d in feriados and spec.get("ignorar_feriados", True):
                    raise CommandError(
                        f"{d:%d/%m/%Y} é feriado, e esta disciplina está marcada para "
                        "não ter aula em feriado. Se ela REALMENTE tem aula nesse dia, "
                        'ponha "ignorar_feriados": false no JSON; se não tem, marque a '
                        'data como "sem_aula" ou tire-a da lista.'
                    )
                raise CommandError(
                    f"{d:%d/%m/%Y} ({DIAS_NOME[d.weekday()]}) não é dia de aula desta "
                    f"disciplina, ou está fora de {data_inicio:%d/%m}–{data_fim:%d/%m}. "
                    "A ocorrência ficaria no banco sem nunca aparecer no calendário."
                )
            do_json[d] = item

        classe_prova = None
        if any("prova" in i for i in spec["datas"]):
            classe_prova = self._resolver_classe(dono, CLASSE_PROVA)

        for d, item in sorted(do_json.items()):
            self._gravar_conteudo(geradas[d], d, item, classe_prova, rel)

        # --- datas que a série gera e o JSON não lista ---------------------
        # NÃO são puladas: a série continua mandando em quando há aula. Viram
        # aviso, porque quase sempre significam transcrição incompleta.
        rel["sem_conteudo"] = sorted(d for d in geradas if d not in do_json)

        rel["removidas"] = self._remover_orfas(geradas, do_json, eventos_por_dia, rel)
        rel["conferencia"] = self._conferir_tarefas(dono, do_json)
        return rel

    def _upsert_serie(
        self, dono, classe, titulo, disc, spec, rec, data_inicio, data_fim
    ):
        """Cria ou atualiza UMA recorrência da disciplina, achada pela chave."""
        dias = sorted(rec["dias"])
        chave = f"{disc['codigo']}-{spec['semestre']}-{'_'.join(map(str, dias))}"

        primeiro = _primeira_data(data_inicio, dias)
        inicio = _local(primeiro, rec["inicio"])
        fim = _local(primeiro, rec["fim"])

        evento = Evento.objects.do_dono(dono).filter(chave_importacao=chave).first()
        criado = evento is None
        if criado:
            evento = Evento(dono=dono, chave_importacao=chave)

        regra = evento.regra_recorrencia or RegraRecorrencia(dono=dono)
        regra.tipo = RegraRecorrencia.Tipo.SEMANAL
        regra.dias = dias
        # Escolha da disciplina, não deste comando. Default `True`, igual às
        # séries lançadas à mão: feriado não tem aula, salvo se o planejamento de
        # ensino disser que tem.
        regra.ignorar_feriados = spec.get("ignorar_feriados", True)
        regra.data_fim = data_fim
        # `validate_unique=False` nos dois models-raiz: a checagem de unicidade do
        # Django passa pelo manager DEFAULT, que aqui é o `EscopoManager` e recusa
        # consulta sem dono (0B.10) — `full_clean()` cheio levantaria `EscopoAusente`.
        # A unicidade de `chave_importacao` continua garantida, e por um mecanismo
        # mais forte: a constraint parcial no banco (princípio 9).
        regra.full_clean(validate_unique=False)
        regra.save()

        evento.titulo = titulo
        evento.descricao = disc.get("descricao", "")
        evento.inicio = inicio
        evento.fim = fim
        evento.classe = classe
        evento.rastrear_conclusao = disc.get("rastrear_conclusao", False)
        evento.regra_recorrencia = regra
        evento.full_clean(validate_unique=False)
        evento.save()
        return evento, criado

    def _datas_geradas(self, evento, data_inicio, data_fim, feriados):
        """As datas que a REGRA produz — as únicas em que uma aula pode aparecer.

        `datas_da_regra` e não `expandir`: o segundo já aplica os overrides, e a
        data que a importação anterior marcou como PULADO desapareceria da lista
        — a segunda execução rejeitaria o próprio JSON que gerou a primeira.

        Os feriados entram porque a série pode ignorá-los: se ignora, aquele dia
        não existe para ela, e conteúdo gravado ali nunca apareceria.
        """
        janela_ini = _local(data_inicio, "00:00")
        janela_fim = _local(data_fim, "23:59")
        return datas_da_regra(evento, janela_ini, janela_fim, feriados)

    # ------------------------------------------------------------------ #
    # Ocorrências                                                        #
    # ------------------------------------------------------------------ #
    def _gravar_conteudo(self, evento, data, item, classe_prova, rel):
        oc = self._ocorrencia(evento, data)

        if "sem_aula" in item:
            oc.status_override = "PULADO"
            oc.descricao_override = item["sem_aula"]
            oc.titulo_override = ""
            oc.classe_override = None
            rel["sem_aula"] += 1
        else:
            oc.descricao_override = item.get("conteudo", "")
            if "prova" in item:
                oc.titulo_override = item["prova"]
                oc.classe_override = classe_prova
                rel["provas"] += 1
            elif "entrega" in item:
                # Entrega NÃO muda a classe (D2): se entrega e prova tiverem a
                # mesma cor, "vermelho" deixa de querer dizer prova.
                oc.titulo_override = item["entrega"]
                oc.classe_override = None
                rel["entregas"] += 1
            else:
                oc.titulo_override = ""
                oc.classe_override = None
            rel["conteudo"] += 1
            # Uma data que o usuário concluiu e o JSON agora descreve mantém o
            # CONCLUIDO: o importador escreve conteúdo, não desfaz o que ele fez.
            if oc.status_override == "PULADO":
                oc.status_override = None

        self._salvar(oc)

    def _ocorrencia(self, evento, data):
        return Ocorrencia.objects.filter(
            evento=evento, data=data
        ).first() or Ocorrencia(evento=evento, data=data)

    def _salvar(self, oc):
        try:
            # É o `full_clean` que aciona a checagem de dono da `classe_override`
            # (PR A) — sem ele, a classe de um perfil entraria no calendário de
            # outro pelo importador.
            oc.full_clean()
        except ValidationError as e:
            raise CommandError(f"Ocorrência de {oc.data:%d/%m/%Y} inválida: {e}") from e
        oc.save()

    def _remover_orfas(self, geradas, do_json, eventos_por_dia, rel):
        """Ocorrências que este comando escreveu e o JSON não pede mais.

        Dois casos: a data saiu da lista (o conteúdo dela tem de sumir junto,
        senão "corrigir o arquivo e re-rodar" não limparia nada) e a data deixou
        de ser gerada (o `data_fim` encolheu, ou o dia da semana mudou).

        Só apaga o que não tem estado do usuário — concluído/remarcado fica e
        vira aviso.
        """
        removidas = 0
        for evento in dict.fromkeys(eventos_por_dia.values()):
            manter = {d for d, ev in geradas.items() if ev == evento and d in do_json}
            orfas = Ocorrencia.objects.filter(evento=evento).exclude(data__in=manter)
            for oc in orfas:
                if oc.status_override in ("CONCLUIDO", "REMARCADO"):
                    rel["avisos"].append(
                        f"{oc.data:%d/%m} está {oc.status_override.lower()} e o "
                        "planejamento não a lista mais — mantida como está."
                    )
                    continue
                oc.delete()
                removidas += 1
        return removidas

    # ------------------------------------------------------------------ #
    # Substituição dos avulsos                                           #
    # ------------------------------------------------------------------ #
    def _apagar_avulsos(self, dono, spec, data_inicio, data_fim, rel):
        qs = self._avulsos(dono, spec, data_inicio, data_fim)

        travados = qs.annotate(n_oc=Count("ocorrencias")).filter(
            status__isnull=False
        ) | qs.annotate(n_oc=Count("ocorrencias")).filter(n_oc__gt=0)
        travados = travados | qs.filter(origem_tarefa__isnull=False)
        travados = travados.distinct()
        if travados.exists():
            linhas = "\n".join(
                f"  - {timezone.localtime(e.inicio):%d/%m %H:%M} {e.titulo}"
                for e in travados[:10]
            )
            raise CommandError(
                f"{travados.count()} evento(s) avulso(s) têm estado do usuário "
                f"(concluído, remarcado, ocorrência ou tarefa de origem):\n{linhas}\n"
                "Apagá-los perderia esse estado. Resolva-os à mão antes de substituir."
            )

        n = qs.count()
        for e in qs.order_by("inicio"):
            rel["avisos"].append(
                f"apaga  {timezone.localtime(e.inicio):%d/%m %H:%M}  {e.titulo}"
            )
        qs.delete()
        return n

    def _avulsos(self, dono, spec, data_inicio, data_fim):
        prefixo = spec.get("prefixo_avulsos") or f"{spec['disciplina']['sigla']} — "
        return Evento.objects.do_dono(dono).filter(
            regra_recorrencia__isnull=True,
            classe__nome__in=CLASSES_SUBSTITUIVEIS,
            titulo__startswith=prefixo,
            inicio__gte=_local(data_inicio, "00:00"),
            inicio__lte=_local(data_fim, "23:59"),
        )

    # ------------------------------------------------------------------ #
    # Conferência (relata, não escreve)                                  #
    # ------------------------------------------------------------------ #
    def _conferir_tarefas(self, dono, do_json):
        """Prova/entrega do JSON sem `Tarefa` com deadline naquele dia.

        Relatório, nunca escrita (§3.4 do plano-pai): o esforço mora nas tarefas
        que já existem, e criar tarefa aqui faria deste comando o lugar onde a
        próxima duplicata nasce.
        """
        from planner.models import Tarefa

        faltando = []
        for d, item in sorted(do_json.items()):
            rotulo = item.get("prova") or item.get("entrega")
            if not rotulo:
                continue
            existe = Tarefa.objects.do_dono(dono).filter(deadline__date=d).exists()
            if not existe:
                faltando.append((d, rotulo))
        return faltando

    # ------------------------------------------------------------------ #
    # Relatório                                                          #
    # ------------------------------------------------------------------ #
    def _imprimir(self, rel, spec):
        disc = spec["disciplina"]
        self.stdout.write(f"\n{disc['nome']} ({disc['codigo']}) — {spec['semestre']}")

        if rel["avulsos_apagados"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{rel['avulsos_apagados']} evento(s) avulso(s) apagado(s):"
                )
            )

        for aviso in rel["avisos"]:
            self.stdout.write(f"  {aviso}")

        self.stdout.write("\nSéries:")
        for titulo, dias, criado in rel["series"]:
            marca = "criada " if criado else "atualiz"
            nomes = "/".join(DIAS_NOME[d] for d in dias)
            self.stdout.write(f"  {marca}  {titulo}  ({nomes})")

        self.stdout.write("\nOcorrências:")
        self.stdout.write(f"  conteúdo ......... {rel['conteudo']}")
        self.stdout.write(f"    das quais prova .. {rel['provas']}")
        self.stdout.write(f"    das quais entrega. {rel['entregas']}")
        self.stdout.write(f"  sem aula (JSON) .. {rel['sem_aula']}")
        if rel["removidas"]:
            self.stdout.write(f"  removidas ........ {rel['removidas']}")

        if rel["sem_conteudo"]:
            n = len(rel["sem_conteudo"])
            self.stdout.write(
                self.style.WARNING(
                    f"\n{n} dia(s) de aula sem conteúdo transcrito — a aula CONTINUA "
                    "no calendário,\nsó fica sem a descrição do dia:"
                )
            )
            for d in rel["sem_conteudo"][:15]:
                self.stdout.write(f"  {d:%d/%m} ({DIAS_NOME[d.weekday()]})")
            if n > 15:
                self.stdout.write(f"  ... e mais {n - 15}")
            self.stdout.write(
                '  Se algum desses dias NÃO tem aula, marque-o como "sem_aula".'
            )

        if rel["conferencia"]:
            self.stdout.write(
                self.style.WARNING("\nProva/entrega sem tarefa com esse prazo:")
            )
            for d, rotulo in rel["conferencia"]:
                self.stdout.write(f"  {d:%d/%m}  {rotulo}")
            self.stdout.write("  (o comando não cria tarefa — isto é conferência.)")


# ---------------------------------------------------------------------- #
# Validação do JSON e utilitários de data                                #
# ---------------------------------------------------------------------- #
def validar_spec(spec):
    """Valida a forma do JSON inteiro ANTES de tocar no banco.

    Erro de transcrição é a falha esperada aqui, não a exceção — então a
    mensagem tem de dizer qual campo e qual valor.
    """
    if not isinstance(spec, dict):
        raise CommandError("O JSON deve ser um objeto.")

    for campo in ("disciplina", "semestre", "recorrencias", "data_inicio", "data_fim"):
        if campo not in spec:
            raise CommandError(f"Campo obrigatório ausente: {campo!r}.")

    for campo in ("codigo", "nome", "sigla", "classe"):
        if not spec["disciplina"].get(campo):
            raise CommandError(f"disciplina.{campo} é obrigatório.")

    try:
        inicio = date.fromisoformat(spec["data_inicio"])
        fim = date.fromisoformat(spec["data_fim"])
    except (TypeError, ValueError) as e:
        raise CommandError(f"data_inicio/data_fim devem ser AAAA-MM-DD: {e}") from e
    if fim < inicio:
        raise CommandError("data_fim é anterior a data_inicio.")

    recorrencias = spec["recorrencias"]
    if not isinstance(recorrencias, list) or not recorrencias:
        raise CommandError("recorrencias deve ser uma lista não-vazia.")

    vistos = {}
    for i, rec in enumerate(recorrencias):
        dias = rec.get("dias")
        if not isinstance(dias, list) or not dias:
            raise CommandError(f"recorrencias[{i}].dias deve ser uma lista não-vazia.")
        for d in dias:
            if not isinstance(d, int) or not 0 <= d <= 6:
                raise CommandError(
                    f"recorrencias[{i}].dias tem {d!r}; use 0=seg … 6=dom."
                )
            if d in vistos:
                raise CommandError(
                    f"{DIAS_NOME[d]} aparece em duas recorrências "
                    f"({vistos[d]} e {i}). Cada dia da semana tem de ter um horário só "
                    "— não dá para saber em qual série a data entraria."
                )
            vistos[d] = i
        h_ini = _hora(rec.get("inicio"), f"recorrencias[{i}].inicio")
        h_fim = _hora(rec.get("fim"), f"recorrencias[{i}].fim")
        if h_fim <= h_ini:
            raise CommandError(f"recorrencias[{i}]: fim não é depois do início.")

    datas = spec.get("datas", [])
    if not isinstance(datas, list):
        raise CommandError("datas deve ser uma lista.")
    vistas = set()
    for i, item in enumerate(datas):
        try:
            d = date.fromisoformat(item["data"])
        except (KeyError, TypeError, ValueError) as e:
            raise CommandError(f"datas[{i}].data inválida: {e}") from e
        if d in vistas:
            raise CommandError(f"{d:%d/%m/%Y} aparece duas vezes em datas.")
        vistas.add(d)
        if "prova" in item and "entrega" in item:
            raise CommandError(
                f"{d:%d/%m/%Y} tem prova e entrega juntas. Separe: a prova troca a "
                "classe do dia, a entrega não."
            )


def _hora(valor, campo):
    try:
        return time.fromisoformat(valor)
    except (TypeError, ValueError) as e:
        raise CommandError(f"{campo} deve ser HH:MM: {valor!r}") from e


def _local(dia, hora):
    """Data + hora LOCAL viram datetime aware.

    O banco guarda UTC e a grade do usuário é local (ASL às 15:50 está gravada
    como 18:50). Sem esta conversão a aula entra 3 horas deslocada, em silêncio.
    """
    if isinstance(hora, str):
        hora = time.fromisoformat(hora)
    return timezone.make_aware(datetime.combine(dia, hora))


def _primeira_data(data_inicio, dias):
    """Primeira data >= data_inicio que cai num dos dias da semana da regra.

    É o `dtstart` da rrule: começar antes faria a série gerar datas fora do
    semestre.
    """
    for i in range(7):
        d = data_inicio + timedelta(days=i)
        if d.weekday() in dias:
            return d
    raise CommandError(f"Nenhum dos dias {dias} cai na semana de {data_inicio}.")
