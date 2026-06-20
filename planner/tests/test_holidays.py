"""Testes de services/holidays: cache e degradação graciosa."""

import datetime

import pytest
from django.core.cache import cache

from planner.services import holidays


@pytest.fixture(autouse=True)
def _cache_limpo():
    cache.clear()
    yield
    cache.clear()


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

    assert resultado == {datetime.date(2026, 1, 1)}


def test_degradacao_retorna_vazio_sem_stale(monkeypatch):
    def boom(url, timeout):
        raise RuntimeError("sem rede")

    monkeypatch.setattr(holidays.requests, "get", boom)
    assert holidays.feriados_do_ano(2099) == set()
