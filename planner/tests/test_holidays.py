"""Testes de services/holidays: cache, degradação e camadas regionais (C8).

`feriados_do_ano` agora mescla nacional ∪ estadual ∪ municipal e a camada
municipal lê o DB — por isso o módulo inteiro é django_db. A seed de Curitiba
(migration 0006) está presente no banco de teste.
"""

import datetime

import pytest
from django.core.cache import cache

from planner.models import FeriadoLocal
from planner.services import holidays

pytestmark = pytest.mark.django_db

CURITIBA = datetime.date(2026, 9, 8)  # seed: Nossa Senhora da Luz dos Pinhais


@pytest.fixture(autouse=True)
def _cache_limpo():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def _sem_rede(monkeypatch):
    def boom(url, timeout):
        raise RuntimeError("sem rede")

    monkeypatch.setattr(holidays.requests, "get", boom)


class _FakeResp:
    def __init__(self, dados):
        self._dados = dados

    def raise_for_status(self):
        pass

    def json(self):
        return self._dados


def test_busca_e_cacheia(monkeypatch):
    chamadas = {"n": 0}

    def fake_get(url, timeout):
        chamadas["n"] += 1
        return _FakeResp([{"date": "2026-01-01"}, {"date": "2026-12-25"}])

    monkeypatch.setattr(holidays.requests, "get", fake_get)

    primeiro = holidays.feriados_do_ano(2026)
    segundo = holidays.feriados_do_ano(2026)

    assert datetime.date(2026, 1, 1) in primeiro
    assert primeiro == segundo
    assert chamadas["n"] == 1  # segunda chamada veio do cache


def test_degradacao_usa_cache_stale(monkeypatch):
    def ok(url, timeout):
        return _FakeResp([{"date": "2026-01-01"}])

    monkeypatch.setattr(holidays.requests, "get", ok)
    holidays.feriados_do_ano(2026)  # popula fresco + stale

    cache.delete("feriados:2026")  # expira só o cache fresco

    def boom(url, timeout):
        raise RuntimeError("sem rede")

    monkeypatch.setattr(holidays.requests, "get", boom)
    resultado = holidays.feriados_do_ano(2026)

    assert datetime.date(2026, 1, 1) in resultado  # veio da cópia stale


def test_degradacao_nacional_retorna_vazio_sem_stale(_sem_rede):
    assert holidays._nacionais(2099) == set()


# --------------------------------------------------------------------------- #
# Municipal (FeriadoLocal, Marco C8)                                           #
# --------------------------------------------------------------------------- #
def test_seed_curitiba_entra_no_merge(_sem_rede):
    assert CURITIBA in holidays.feriados_do_ano(2026)
    # recorre todo ano (ano nulo na seed)
    assert datetime.date(2031, 9, 8) in holidays.feriados_do_ano(2031)


def test_municipal_pontual_so_vale_no_ano(_sem_rede):
    FeriadoLocal.objects.create(nome="Decretado", dia=2, mes=1, ano=2026)
    assert datetime.date(2026, 1, 2) in holidays.feriados_do_ano(2026)
    assert datetime.date(2027, 1, 2) not in holidays.feriados_do_ano(2027)


def test_municipal_29_de_fevereiro_pula_ano_nao_bissexto(_sem_rede):
    FeriadoLocal.objects.create(nome="Bissexto", dia=29, mes=2)
    assert datetime.date(2028, 2, 29) in holidays.feriados_do_ano(2028)
    # 2026 não é bissexto: a data não existe — pula sem levantar. (Sem igualdade
    # exata de conjunto: rodando com o stack de dev no ar, o web vivo pode
    # reescrever o cache de feriados nacionais entre o clear e o assert.)
    r2026 = holidays.feriados_do_ano(2026)
    assert not any(f.month == 2 and f.day == 29 for f in r2026)
    assert CURITIBA in r2026


# --------------------------------------------------------------------------- #
# Estadual (FERIADOS_UF via lib offline, Marco C8)                             #
# --------------------------------------------------------------------------- #
def test_estadual_por_uf(_sem_rede, settings):
    settings.FERIADOS_UF = "SP"
    # 9 de julho (Revolução Constitucionalista) é feriado estadual de SP.
    assert datetime.date(2026, 7, 9) in holidays.feriados_do_ano(2026)


def test_estadual_desligado_por_padrao(_sem_rede, settings):
    settings.FERIADOS_UF = ""
    assert datetime.date(2026, 7, 9) not in holidays.feriados_do_ano(2026)


def test_estadual_uf_invalida_degrada_sem_excecao(_sem_rede, settings):
    settings.FERIADOS_UF = "XX"
    resultado = holidays.feriados_do_ano(2026)  # não levanta
    assert CURITIBA in resultado  # as demais camadas seguem de pé
