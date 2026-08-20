"""Testes de services/recurrence: expansão via rrule e overrides."""

import datetime

import pytest

from planner.models import Evento, RegraRecorrencia
from planner.services.recurrence import datas_da_regra, expandir

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


# --------------------------------------------------------------------------- #
# datas_da_regra — a pergunta "este dia é dia de aula?" (Fase 1.2 / PR B)      #
# --------------------------------------------------------------------------- #
def test_datas_da_regra_devolve_as_datas_cruas():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0])  # segundas
    datas = datas_da_regra(ev, aware(2026, 6, 1), aware(2026, 6, 22, 23, 59), set())

    assert [d.isoformat() for d in datas] == [
        "2026-06-01",
        "2026-06-08",
        "2026-06-15",
        "2026-06-22",
    ]


def test_datas_da_regra_mantem_a_data_pulada():
    """O contrário de `expandir`, e é o ponto da função.

    O importador precisa saber que 08/06 É dia de aula mesmo tendo sido marcado
    como PULADO — senão a segunda importação rejeitaria o JSON que a primeira
    gerou (bug achado no PR B).
    """
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0])
    OcorrenciaFactory(
        evento=ev, data=datetime.date(2026, 6, 8), status_override="PULADO"
    )
    janela = (aware(2026, 6, 1), aware(2026, 6, 15, 23, 59))

    cruas = datas_da_regra(ev, *janela, set())
    visiveis = [v.data for v in expandir(ev, *janela, set())]

    assert datetime.date(2026, 6, 8) in cruas
    assert datetime.date(2026, 6, 8) not in visiveis


def test_datas_da_regra_respeita_feriado_quando_a_regra_ignora():
    ev = _recorrente(RegraRecorrencia.Tipo.SEMANAL, [0], ignorar_feriados=True)
    feriados = {datetime.date(2026, 6, 8)}

    datas = datas_da_regra(ev, aware(2026, 6, 1), aware(2026, 6, 15, 23, 59), feriados)

    assert datetime.date(2026, 6, 8) not in datas


def test_datas_da_regra_em_evento_avulso_e_vazia():
    ev = EventoFactory(inicio=aware(2026, 6, 1, 8), fim=aware(2026, 6, 1, 10))

    assert datas_da_regra(ev, aware(2026, 6, 1), aware(2026, 6, 30), set()) == []
