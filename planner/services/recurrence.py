"""Expansão de recorrência em ocorrências virtuais (Handoff §6).

Um evento recorrente guarda inicio/fim da primeira ocorrência (hora do dia +
duração) e uma RegraRecorrencia. As datas concretas são geradas sob demanda com
dateutil.rrule, SEMPRE dentro de uma janela limitada — nunca série infinita.
"""

from dataclasses import dataclass
from datetime import date, datetime, time

from dateutil.rrule import MONTHLY, WEEKLY, rrule
from django.db.models import Prefetch

from ..models import Evento, Ocorrencia, RegraRecorrencia


def prefetch_ocorrencias():
    """Prefetch das ocorrências de um queryset de eventos, pronto para expandir.

    Duas coisas num objeto só, e as duas importam: o `Prefetch` (em vez de
    modificar o queryset dentro de `expandir`, o que descartaria o cache e faria
    1 query por evento) e o `select_related` da classe do override (sem ele,
    1 query por dia de prova). Use SEMPRE que for chamar `expandir`.
    """
    return Prefetch(
        "ocorrencias", queryset=Ocorrencia.objects.select_related("classe_override")
    )


@dataclass
class OcorrenciaView:
    """Ocorrência (virtual ou materializada) pronta para serializar.

    Expõe rastrear_conclusao/status/fim para completion.status_efetivo.

    `titulo`/`descricao`/`classe` são os EFETIVOS daquela data (Fase 1.2): o
    override quando existe, o da série quando não. Quem consome não decide nada
    — a resolução é aqui, uma vez, para a view e o agente lerem igual.
    """

    evento: Evento
    data: date
    inicio: datetime
    fim: datetime
    rastrear_conclusao: bool
    status: str | None
    persistida: bool
    titulo: str
    descricao: str
    classe: object  # Classe — efetiva do dia (a da série, ou o override)


def montar_ocorrencia(evento, dt, duracao, persistidas):
    """Monta a OcorrenciaView de uma data, aplicando overrides persistidos.

    `persistidas` é um dict {data: Ocorrencia}. Retorna None se a ocorrência foi
    PULADA (override que a omite).
    """
    data = dt.date()
    ocorrencia = persistidas.get(data)
    inicio = dt
    fim = dt + duracao
    status = None
    # Default de todo campo efetivo: o da série. O override só entra se houver.
    titulo = evento.titulo
    descricao = evento.descricao
    classe = evento.classe
    if ocorrencia is not None:
        if ocorrencia.status_override == "PULADO":
            return None
        if ocorrencia.inicio_override:
            inicio = ocorrencia.inicio_override
        if ocorrencia.fim_override:
            fim = ocorrencia.fim_override
        status = ocorrencia.status_override
        # `blank=True` e não nulo: string vazia é "não disse nada", que herda.
        # Assim apagar um conteúdo é gravar "", sem precisar de sentinela.
        if ocorrencia.titulo_override:
            titulo = ocorrencia.titulo_override
        if ocorrencia.descricao_override:
            descricao = ocorrencia.descricao_override
        if ocorrencia.classe_override_id:
            classe = ocorrencia.classe_override
    return OcorrenciaView(
        evento=evento,
        data=data,
        inicio=inicio,
        fim=fim,
        rastrear_conclusao=evento.rastrear_conclusao,
        status=status,
        persistida=ocorrencia is not None,
        titulo=titulo,
        descricao=descricao,
        classe=classe,
    )


def expandir(evento, janela_inicio, janela_fim, feriados):
    """Gera as ocorrências de `evento` dentro de [janela_inicio, janela_fim].

    `feriados` é um set[date]; se a regra ignora feriados, datas coincidentes
    são puladas. Limita por regra.data_fim ou pela janela (nunca infinito).
    """
    regra = evento.regra_recorrencia
    if regra is None:
        return

    duracao = evento.fim - evento.inicio

    until = janela_fim
    if regra.data_fim:
        # Fim do dia de data_fim, no fuso do evento.
        data_fim_dt = datetime.combine(
            regra.data_fim, time.max, tzinfo=evento.inicio.tzinfo
        )
        until = min(until, data_fim_dt)

    if regra.tipo == RegraRecorrencia.Tipo.SEMANAL:
        rule = rrule(WEEKLY, dtstart=evento.inicio, until=until, byweekday=regra.dias)
    else:  # MENSAL
        rule = rrule(MONTHLY, dtstart=evento.inicio, until=until, bymonthday=regra.dias)

    # `.all()` e não `.select_related(...)`: qualquer modificação do queryset
    # ignoraria o prefetch de quem chamou e faria 1 query por evento. Quem
    # precisa da classe do override já a traz no Prefetch (agenda/planejamento).
    persistidas = {oc.data: oc for oc in evento.ocorrencias.all()}

    for dt in rule.between(janela_inicio, janela_fim, inc=True):
        if regra.ignorar_feriados and dt.date() in feriados:
            continue
        view = montar_ocorrencia(evento, dt, duracao, persistidas)
        if view is not None:
            yield view
