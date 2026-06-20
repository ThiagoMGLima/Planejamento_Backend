"""Testes de services/recurrence: expansão via rrule e overrides."""

import datetime

import pytest

from planner.models import Evento, RegraRecorrencia
from planner.services.recurrence import expandir

from .factories import EventoFactory, OcorrenciaFactory, RegraRecorrenciaFactory, aware

pytestmark = pytest.mark.django_db


def _recorrente(tipo, dias, **kwargs):
    regra = RegraRecorrenciaFactory(tipo=tipo, dias=dias, **kwargs)
    return EventoFactory(
        inicio=aware(2026, 6, 1, 8),  # 2026-06-01 é uma segunda-feira
        fim=aware(2026, 6, 1, 10),
        regra_recorrencia=regra,
    )


def _datas(views):
    return [v.data.isoformat() for v in views]


def test_expansao_semanal():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0, 2])  # seg, qua
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 6, 14, 23, 59), set()))
    assert _datas(views) == ["2026-06-01", "2026-06-03", "2026-06-08", "2026-06-10"]


def test_expansao_mensal():
    ev = _recorrente(RegraRecorrencia.Tipo.MENSAL, [1, 15])
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 8, 20, 23, 59), set()))
    assert _datas(views) == [
        "2026-06-01",
        "2026-06-15",
        "2026-07-01",
        "2026-07-15",
        "2026-08-01",
        "2026-08-15",
    ]


def test_ignorar_feriados_pula_a_data():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0], ignorar_feriados=True)
    feriados = {datetime.date(2026, 6, 8)}
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 6, 15, 23, 59), feriados))
    assert _datas(views) == ["2026-06-01", "2026-06-15"]


def test_data_fim_limita_a_serie():
    ev = _recorrente(
        RegraRecorrencia.Tipo.SEMANAL, [0], data_fim=datetime.date(2026, 6, 8)
    )
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 6, 30, 23, 59), set()))
    assert _datas(views) == ["2026-06-01", "2026-06-08"]


def test_override_isolado_nao_afeta_a_serie():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0])
    OcorrenciaFactory(
        evento=ev,
        data=datetime.date(2026, 6, 8),
        status_override=Evento.Status.CONCLUIDO,
    )
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 6, 15, 23, 59), set()))
    por_data = {v.data.isoformat(): v for v in views}
    assert por_data["2026-06-08"].status == Evento.Status.CONCLUIDO
    assert por_data["2026-06-08"].persistida is True
    assert por_data["2026-06-01"].status is None
    assert por_data["2026-06-01"].persistida is False


def test_override_pulado_omite_ocorrencia():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0])
    OcorrenciaFactory(
        evento=ev, data=datetime.date(2026, 6, 8), status_override="PULADO"
    )
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 6, 15, 23, 59), set()))
    assert "2026-06-08" not in _datas(views)


def test_override_de_horario():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0])
    OcorrenciaFactory(
        evento=ev,
        data=datetime.date(2026, 6, 8),
        inicio_override=aware(2026, 6, 8, 14),
        fim_override=aware(2026, 6, 8, 16),
    )
    views = list(expandir(ev, aware(2026, 6, 1), aware(2026, 6, 15, 23, 59), set()))
    por_data = {v.data.isoformat(): v for v in views}
    assert por_data["2026-06-08"].inicio == aware(2026, 6, 8, 14)
    assert por_data["2026-06-08"].fim == aware(2026, 6, 8, 16)
