"""Testes do planejamento assistido por IA (Fase A) — Ollama sempre mockado.

Cobre: contexto grounded, guarda-corpo das diretrizes, a chamada de IA (mock),
os alertas (código) e o pipeline da task (feliz, fallback, cache) + os endpoints
com Celery eager. A suíte do solver estendido fica em test_planejamento.py.
"""

import json
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from planner import tasks
from planner.services import planejamento as P
from planner.services import planejamento_ia as IA

from .factories import ClasseFactory, TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    """Isola o cache por teste (não bate no Redis real)."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ia-tests",
        }
    }
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def eager():
    """Roda as tasks de forma síncrona e guarda o resultado p/ o AsyncResult."""
    from config.celery import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    app.conf.task_store_eager_result = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_store_eager_result = False


def _resultado_base(tarefas_entrada, agora=SEG, prefs_entrada=None, horizonte=None):
    """ResultadoPlano puro (sem DB) a partir de TarefaEntrada já montadas."""
    prefs, prefs_usadas = P.montar_preferencias(prefs_entrada or {})
    if horizonte is None:
        horizonte = min(
            max(P._deadline_efetiva(t, agora) for t in tarefas_entrada),
            agora + P.JANELA_MAX,
        )
    sessoes, nao = P.calcular_plano(tarefas_entrada, [], prefs, agora, horizonte)
    return P.ResultadoPlano(
        sessoes=sessoes,
        nao_alocado=nao,
        prefs=prefs,
        prefs_usadas=prefs_usadas,
        tarefas=tarefas_entrada,
        ocupado=[],
        agora=agora,
        horizonte_fim=horizonte,
    )


def _tarefa_valida(**kw):
    defaults = dict(esforco_estimado=240, deadline=aware(2026, 6, 10, 18))
    defaults.update(kw)
    return TarefaFactory(**defaults)


# --------------------------------------------------------------------------- #
# construir_contexto                                                           #
# --------------------------------------------------------------------------- #
def test_construir_contexto_fatos_corretos():
    t = P.TarefaEntrada("A", "Prova", "c1", 300, SEG + timedelta(days=10))
    ctx = IA.construir_contexto(_resultado_base([t]))
    info = ctx["tarefas"][0]
    assert info["id"] == "A"
    assert info["esforco_min"] == 300
    assert info["alocado_min"] == 300
    assert info["restante_min"] == 0
    assert info["sessoes"] >= 1
    assert sum(ctx["carga_por_dia"].values()) == 300
    assert ctx["capacidade_livre_antes_da_deadline"]["A"] > 0


def test_construir_contexto_restante_vem_do_nao_alocado():
    # Esforço gigante p/ horizonte curtíssimo → sobra restante.
    t = P.TarefaEntrada("A", "Big", "c1", 5000, SEG + timedelta(hours=10))
    ctx = IA.construir_contexto(
        _resultado_base([t], horizonte=SEG + timedelta(hours=10))
    )
    info = ctx["tarefas"][0]
    assert info["restante_min"] == 5000 - info["alocado_min"]
    assert info["restante_min"] > 0
    assert ctx["nao_alocado"][0]["id"] == "A"


def test_construir_contexto_inclui_carga_resumo():
    t = P.TarefaEntrada("A", "Prova", "c1", 300, SEG + timedelta(days=10))
    resumo = IA.construir_contexto(_resultado_base([t]))["carga_resumo"]
    assert resumo["dias_com_carga"] >= 1
    assert resumo["carga_maxima_dia_min"] >= resumo["carga_media_dia_min"] >= 1


# --------------------------------------------------------------------------- #
# estimar_tempo_s                                                              #
# --------------------------------------------------------------------------- #
def test_estimar_tempo_base_mais_por_tarefa(settings):
    settings.PLANEJAR_TEMPO_BASE_S = 50
    settings.PLANEJAR_TEMPO_POR_TAREFA_S = 5
    tarefas = [
        P.TarefaEntrada("A", "A", "c1", 120, SEG + timedelta(days=3)),
        P.TarefaEntrada("B", "B", "c1", 120, SEG + timedelta(days=3)),
    ]
    res = _resultado_base(tarefas)
    assert IA.estimar_tempo_s(res) == 50 + 5 * 2


def test_estimar_tempo_ignora_tarefa_sem_sessao(settings):
    settings.PLANEJAR_TEMPO_BASE_S = 50
    settings.PLANEJAR_TEMPO_POR_TAREFA_S = 5
    # Janela zero (horizonte == agora) → nenhuma sessão alocada.
    t = P.TarefaEntrada("A", "Big", "c1", 5000, SEG + timedelta(days=2))
    res = _resultado_base([t], horizonte=SEG)
    assert {s.tarefa_id for s in res.sessoes} == set()
    assert IA.estimar_tempo_s(res) == 50


# --------------------------------------------------------------------------- #
# validar_diretrizes                                                           #
# --------------------------------------------------------------------------- #
def test_validar_diretrizes_descarta_e_clampa():
    tarefas = [P.TarefaEntrada("A", "A", "c1", 60, SEG + timedelta(days=2))]
    bruto = {
        "prioridades": {"A": 9, "INEXISTENTE": 3},
        "ajustes_por_tarefa": {
            "A": {"buffer_dias": -5, "max_min_por_dia": 0},
            "X": {"buffer_dias": 1},
        },
    }
    limpo = IA.validar_diretrizes(bruto, tarefas)
    assert limpo["prioridades"] == {"A": 5}  # 9 clampado p/ 5; inexistente fora
    assert limpo["ajustes_por_tarefa"]["A"] == {"buffer_dias": 0}  # -5→0; max=0 fora
    assert "X" not in limpo["ajustes_por_tarefa"]  # id inexistente fora


def test_validar_diretrizes_aceita_validos():
    tarefas = [P.TarefaEntrada("A", "A", "c1", 60, SEG + timedelta(days=5))]
    limpo = IA.validar_diretrizes(
        {"prioridades": {"A": 3}, "ajustes_por_tarefa": {"A": {"max_min_por_dia": 60}}},
        tarefas,
    )
    assert limpo == {
        "prioridades": {"A": 3},
        "ajustes_por_tarefa": {"A": {"max_min_por_dia": 60}},
    }


def test_validar_diretrizes_aceita_teto_total():
    tarefas = [P.TarefaEntrada("A", "A", "c1", 60, SEG + timedelta(days=5))]
    limpo = IA.validar_diretrizes({"max_min_por_dia_total": 90}, tarefas)
    assert limpo["max_min_por_dia_total"] == 90


def test_validar_diretrizes_descarta_teto_total_invalido():
    tarefas = [P.TarefaEntrada("A", "A", "c1", 60, SEG + timedelta(days=5))]
    for bruto in ({"max_min_por_dia_total": 0}, {"max_min_por_dia_total": "x"}):
        assert "max_min_por_dia_total" not in IA.validar_diretrizes(bruto, tarefas)


# ------------------------- alavancas de cenário (C1a) ----------------------- #
_TAREFAS = [P.TarefaEntrada("A", "A", "c1", 60, SEG + timedelta(days=5))]
_HORIZONTE = SEG + timedelta(days=7)


def test_validar_janela_por_dia_aceita_semana_e_data_no_horizonte():
    bruto = {
        "janela_por_dia": {
            "3": ["08:00", "20:00"],  # dia da semana
            "2026-06-04": ["09:00", "19:00"],  # data dentro do horizonte
        }
    }
    limpo = IA.validar_diretrizes(bruto, _TAREFAS, SEG, _HORIZONTE)
    assert limpo["janela_por_dia"] == {
        "3": ["08:00", "20:00"],
        "2026-06-04": ["09:00", "19:00"],
    }


def test_validar_janela_por_dia_descarta_entradas_invalidas():
    bruto = {
        "janela_por_dia": {
            "7": ["08:00", "20:00"],  # dia da semana inexistente
            "2026-09-01": ["08:00", "20:00"],  # fora do horizonte
            "0": ["04:00", "20:00"],  # antes de 05:00
            "1": ["20:00", "08:00"],  # ini ≥ fim
            "2": ["08:00"],  # shape errado
            "4": "08:00-20:00",  # não é lista
        }
    }
    limpo = IA.validar_diretrizes(bruto, _TAREFAS, SEG, _HORIZONTE)
    assert "janela_por_dia" not in limpo


def test_validar_usar_fds_so_aceita_bool_literal():
    assert IA.validar_diretrizes({"usar_fds": True}, _TAREFAS)["usar_fds"] is True
    assert IA.validar_diretrizes({"usar_fds": False}, _TAREFAS)["usar_fds"] is False
    for ruim in ("sim", 1, None, [True]):
        assert "usar_fds" not in IA.validar_diretrizes({"usar_fds": ruim}, _TAREFAS)


def test_validar_dias_bloqueados_horizonte_dedup_e_teto():
    bruto = {
        "dias_bloqueados": ["2026-06-03", "2026-06-03", "2026-09-01", "x", "2026-06-04"]
    }
    limpo = IA.validar_diretrizes(bruto, _TAREFAS, SEG, _HORIZONTE)
    assert limpo["dias_bloqueados"] == ["2026-06-03", "2026-06-04"]
    # Máximo 14 datas; excedente descartado.
    muitos = [(SEG + timedelta(days=d)).date().isoformat() for d in range(20)]
    limpo = IA.validar_diretrizes(
        {"dias_bloqueados": muitos}, _TAREFAS, SEG, SEG + timedelta(days=30)
    )
    assert len(limpo["dias_bloqueados"]) == 14


# --------------------------------------------------------------------------- #
# gerar_melhoria (Ollama mockado)                                             #
# --------------------------------------------------------------------------- #
def test_gerar_melhoria_parse_ok():
    payload = {"diretrizes": {}, "resumo": "ok", "trade_offs": [], "sugestoes": []}
    fake = {"message": {"content": json.dumps(payload)}}
    with mock.patch("planner.services.planejamento_ia.ollama.Client") as Cli:
        Cli.return_value.chat.return_value = fake
        out = IA.gerar_melhoria({"x": 1})
    assert out["resumo"] == "ok"


def test_gerar_melhoria_erro_vira_indisponivel():
    with mock.patch(
        "planner.services.planejamento_ia.ollama.Client",
        side_effect=RuntimeError("down"),
    ):
        with pytest.raises(IA.OllamaIndisponivel):
            IA.gerar_melhoria({})


# --------------------------------------------------------------------------- #
# alertas_do_plano (código, sem IA)                                           #
# --------------------------------------------------------------------------- #
def test_alertas_do_plano_alto_para_nao_alocado():
    t = P.TarefaEntrada("A", "Big", "c1", 5000, SEG + timedelta(hours=10))
    res = _resultado_base([t], horizonte=SEG + timedelta(hours=10))
    alertas = IA.alertas_do_plano(res)
    assert any(a["severidade"] == "alto" and a["tarefa_id"] == "A" for a in alertas)


def test_alertas_do_plano_medio_quando_dia_bloqueado_e_usado():
    from dataclasses import replace
    from datetime import date

    # Deadline hoje com o dia bloqueado → nível 5 usa o dia mesmo assim.
    prefs, prefs_usadas = P.montar_preferencias({})
    prefs = replace(prefs, dias_bloqueados=frozenset({date(2026, 6, 1)}))
    t = P.TarefaEntrada("A", "A", "c1", 60, aware(2026, 6, 1, 22))
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    res = P.ResultadoPlano(sessoes, nao, prefs, prefs_usadas, [t], [], SEG, t.deadline)
    alertas = IA.alertas_do_plano(res)
    assert any(
        a["severidade"] == "medio" and "bloqueado" in a["mensagem"] for a in alertas
    )


# --------------------------------------------------------------------------- #
# Pipeline (task chamada direto; gerar_melhoria mockado)                       #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_pipeline_feliz():
    t = _tarefa_valida(esforco_estimado=300)
    bruto = {
        "diretrizes": {"prioridades": {str(t.id): 5}},
        "resumo": "feito",
        "trade_offs": ["priorizei A"],
        "sugestoes": [{"tipo": "ajustar_pref", "descricao": "d", "acao": {}}],
    }
    with mock.patch("planner.tasks.planejamento_ia.gerar_melhoria", return_value=bruto):
        out = tasks.planejar_ia_task([str(t.id)], SEG.isoformat(), {})

    assert out["ia_indisponivel"] is False
    assert set(out) >= {
        "plano",
        "resumo",
        "trade_offs",
        "alertas",
        "sugestoes",
        "ia_indisponivel",
    }
    assert out["resumo"] == "feito"
    sess = out["plano"]["sessoes"]
    assert sum(s["dur_min"] for s in sess) == 300
    iv = sorted((s["inicio"], s["fim"]) for s in sess)
    assert all(iv[i][1] <= iv[i + 1][0] for i in range(len(iv) - 1))


@pytest.mark.django_db
def test_fallback_quando_ia_indisponivel():
    t = _tarefa_valida(esforco_estimado=120)
    with mock.patch(
        "planner.tasks.planejamento_ia.gerar_melhoria",
        side_effect=IA.OllamaIndisponivel("down"),
    ):
        out = tasks.planejar_ia_task([str(t.id)], SEG.isoformat(), {})
    assert out["ia_indisponivel"] is True
    assert out["resumo"] == ""
    assert sum(s["dur_min"] for s in out["plano"]["sessoes"]) == 120


@pytest.mark.django_db
def test_cache_evita_segunda_chamada_de_ia():
    t = _tarefa_valida(esforco_estimado=120)
    bruto = {"diretrizes": {}, "resumo": "r", "trade_offs": [], "sugestoes": []}
    with mock.patch(
        "planner.tasks.planejamento_ia.gerar_melhoria", return_value=bruto
    ) as m:
        tasks.planejar_ia_task([str(t.id)], SEG.isoformat(), {})
        tasks.planejar_ia_task([str(t.id)], SEG.isoformat(), {})
    assert m.call_count == 1


# --------------------------------------------------------------------------- #
# Endpoints (Celery eager)                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_endpoint_enfileira_retorna_202(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False  # fallback instantâneo, sem tocar Ollama
    t = _tarefa_valida(esforco_estimado=120)
    body = {"tarefa_ids": [str(t.id)], "a_partir_de": SEG.isoformat()}
    resp = api.post("/api/v1/planejamento/planejar-ia", body, format="json")
    assert resp.status_code == 202
    assert resp.data["status"] == "processando"
    assert resp.data["job_id"]


def test_endpoint_status_branches(api):
    """O endpoint de status reflete os 3 estados do AsyncResult."""

    def _fake(successful, failed, result=None):
        m = mock.Mock()
        m.successful.return_value = successful
        m.failed.return_value = failed
        m.result = result
        return m

    with mock.patch(
        "planner.views.AsyncResult",
        return_value=_fake(True, False, {"ia_indisponivel": True}),
    ):
        pronto = api.get("/api/v1/planejamento/planejar-ia/abc")
    assert pronto.data["status"] == "pronto"
    assert pronto.data["resultado"]["ia_indisponivel"] is True

    with mock.patch("planner.views.AsyncResult", return_value=_fake(False, True)):
        erro = api.get("/api/v1/planejamento/planejar-ia/abc")
    assert erro.data["status"] == "erro"

    with mock.patch("planner.views.AsyncResult", return_value=_fake(False, False)):
        proc = api.get("/api/v1/planejamento/planejar-ia/abc")
    assert proc.data["status"] == "processando"


@pytest.mark.django_db
def test_endpoint_cache_hit_retorna_200(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    t = _tarefa_valida(esforco_estimado=120)
    body = {"tarefa_ids": [str(t.id)], "a_partir_de": SEG.isoformat()}
    api.post("/api/v1/planejamento/planejar-ia", body, format="json")  # popula cache
    resp = api.post("/api/v1/planejamento/planejar-ia", body, format="json")
    assert resp.status_code == 200
    assert resp.data["status"] == "pronto"


@pytest.mark.django_db
def test_endpoint_tarefa_invalida_422(api):
    t = TarefaFactory(classe=ClasseFactory(), esforco_estimado=60)  # sem deadline
    resp = api.post(
        "/api/v1/planejamento/planejar-ia",
        {"tarefa_ids": [str(t.id)]},
        format="json",
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_endpoint_sem_tarefa_ids_400(api):
    resp = api.post(
        "/api/v1/planejamento/planejar-ia", {"tarefa_ids": []}, format="json"
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# horizonte (escopo do plano) + estimativa                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_montar_plano_horizonte_limita_escopo():
    # Esforço que estoura a capacidade física de 1 semana (mesmo relaxada: 24h×7 =
    # 10080 min) mas cabe em ~2 meses. Horizonte menor aloca menos e sobra trabalho.
    t = _tarefa_valida(esforco_estimado=20000, deadline=aware(2026, 8, 1, 18))
    auto = P.montar_plano([t], SEG, {})
    semana = P.montar_plano([t], SEG, {}, horizonte_dias=7)
    aloc = lambda r: sum(s.dur_min for s in r.sessoes)  # noqa: E731
    assert semana.horizonte_fim < auto.horizonte_fim
    assert aloc(semana) < aloc(auto)  # janela menor aloca menos
    assert semana.nao_alocado  # e sobra trabalho


def _pico_diario(res):
    """Maior carga (min) somando todas as tarefas num mesmo dia local."""
    por_dia = {}
    for s in res.sessoes:
        dia = timezone.localtime(s.inicio).date()
        por_dia[dia] = por_dia.get(dia, 0) + s.dur_min
    return max(por_dia.values()) if por_dia else 0


@pytest.mark.django_db
def test_teto_total_ia_suaviza_pico_diario():
    # Duas tarefas cabem juntas no 1º dia (240 min); o teto total da IA espalha.
    tA = _tarefa_valida(esforco_estimado=120, deadline=aware(2026, 6, 10, 18))
    tB = _tarefa_valida(esforco_estimado=120, deadline=aware(2026, 6, 10, 18))
    base = P.montar_plano([tA, tB], SEG, {})
    suave = P.montar_plano([tA, tB], SEG, {}, diretrizes={"max_min_por_dia_total": 120})
    assert _pico_diario(suave) <= 120 < _pico_diario(base)


@pytest.mark.django_db
def test_teto_total_ia_nunca_afrouxa_o_do_usuario():
    t = _tarefa_valida(esforco_estimado=300, deadline=aware(2026, 6, 20, 18))
    res = P.montar_plano(
        [t],
        SEG,
        {"max_min_por_dia_total": 60},
        diretrizes={"max_min_por_dia_total": 120},  # IA tenta afrouxar → ignorado
    )
    assert res.prefs_usadas["max_min_por_dia_total"] == 60


@pytest.mark.django_db
def test_endpoint_202_inclui_tempo_estimado(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    t = _tarefa_valida(esforco_estimado=120)
    body = {
        "tarefa_ids": [str(t.id)],
        "a_partir_de": SEG.isoformat(),
        "horizonte": "MES",
    }
    resp = api.post("/api/v1/planejamento/planejar-ia", body, format="json")
    assert resp.status_code == 202
    assert resp.data["tempo_estimado_s"] >= settings.PLANEJAR_TEMPO_BASE_S


@pytest.mark.django_db
def test_endpoint_horizonte_invalido_400(api):
    t = _tarefa_valida(esforco_estimado=120)
    body = {"tarefa_ids": [str(t.id)], "horizonte": "DECADA"}
    resp = api.post("/api/v1/planejamento/planejar-ia", body, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_estimativa_endpoint_responde(api):
    # Endpoint usa now() real → deadline precisa estar no futuro para alocar.
    t = _tarefa_valida(esforco_estimado=120, deadline=aware(2026, 12, 1, 18))
    resp = api.get(
        "/api/v1/planejamento/planejar-ia/estimativa",
        {"tarefa_ids": [str(t.id)], "horizonte": "SEMANA"},
    )
    assert resp.status_code == 200
    assert resp.data["n_tarefas_no_escopo"] == 1
    assert resp.data["tempo_estimado_s"] >= 1


@pytest.mark.django_db
def test_estimativa_horizonte_invalido_400(api):
    resp = api.get(
        "/api/v1/planejamento/planejar-ia/estimativa", {"horizonte": "DECADA"}
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_estimativa_sem_tarefas_validas_usa_base(api, settings):
    settings.PLANEJAR_TEMPO_BASE_S = 42
    resp = api.get("/api/v1/planejamento/planejar-ia/estimativa")
    assert resp.status_code == 200
    assert resp.data == {"n_tarefas_no_escopo": 0, "tempo_estimado_s": 42}
