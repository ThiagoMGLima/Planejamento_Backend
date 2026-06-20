"""Expansão de recorrência em ocorrências virtuais (Handoff §6).

Um evento recorrente guarda inicio/fim da primeira ocorrência (hora do dia +
duração) e uma RegraRecorrencia. As datas concretas são geradas sob demanda com
dateutil.rrule, SEMPRE dentro de uma janela limitada — nunca série infinita.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from dateutil.rrule import MONTHLY, WEEKLY, rrule

from ..models import Evento, Ocorrencia, RegraRecorrencia


@dataclass
class OcorrenciaView:
    """Ocorrência (virtual ou materializada) pronta para serializar.

    Expõe rastrear_conclusao/status/fim para completion.status_efetivo.
    """

    evento: Evento
    data: date
    inicio: datetime
    fim: datetime
    rastrear_conclusao: bool
    status: str | None
    persistida: bool


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
    if ocorrencia is not None:
        if ocorrencia.status_override == "PULADO":
            return None
        if ocorrencia.inicio_override:
            inicio = ocorrencia.inicio_override
        if ocorrencia.fim_override:
            fim = ocorrencia.fim_override
        status = ocorrencia.status_override
    return OcorrenciaView(
        evento=evento,
        data=data,
        inicio=inicio,
        fim=fim,
        rastrear_conclusao=evento.rastrear_conclusao,
        status=status,
        persistida=ocorrencia is not None,
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
        rule = rrule(
            WEEKLY, dtstart=evento.inicio, until=until, byweekday=regra.dias
        )
    else:  # MENSAL
        rule = rrule(
            MONTHLY, dtstart=evento.inicio, until=until, bymonthday=regra.dias
        )

    persistidas = {oc.data: oc for oc in evento.ocorrencias.all()}

    for dt in rule.between(janela_inicio, janela_fim, inc=True):
        if regra.ignorar_feriados and dt.date() in feriados:
            continue
        view = montar_ocorrencia(evento, dt, duracao, persistidas)
        if view is not None:
            yield view
