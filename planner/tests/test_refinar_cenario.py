"""Testes do refino conversacional de cenários (Marco C5) — Ollama mockado.

Cobre: a alavanca `excluir_tarefas` (guarda-corpo e solver), o fluxo
202→polling→pronto do refinar, o cenário novo anexado ao lote original (com
`escolher` funcionando), a memória da conversa e a degradação sem IA.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.cache import cache as django_cache
from rest_framework.test import APIClient

from planner.services import planejamento as P
from planner.services.planejamento_ia import OllamaIndisponivel, validar_diretrizes

from .factories import TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "refinar-tests",
        }
    }
    django_cache.clear()
    yield
    django_cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def eager():
    from config.celery import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    app.conf.task_store_eager_result = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_store_eager_result = False


# --------------------------------------------------------------------------- #
# Alavanca excluir_tarefas (guarda-corpo e solver)                             #
# --------------------------------------------------------------------------- #
def test_validar_diretrizes_excluir_tarefas_filtra_ids():
    tarefas = [SimpleNamespace(id="A"), SimpleNamespace(id="B")]
    out = validar_diretrizes({"excluir_tarefas": ["A", "A", "inexistente"]}, tarefas)
    assert out["excluir_tarefas"] == ["A"]


def test_validar_diretrizes_excluir_todas_e_descartado():
    tarefas = [SimpleNamespace(id="A"), SimpleNamespace(id="B")]
    out = validar_diretrizes({"excluir_tarefas": ["A", "B"]}, tarefas)
    assert "excluir_tarefas" not in out
    out = validar_diretrizes({"excluir_tarefas": "A"}, tarefas)  # shape errado
    assert "excluir_tarefas" not in out


@pytest.mark.django_db
def test_montar_plano_excluir_tarefas_mantem_horizonte():
    perto = TarefaFactory(esforco_estimado=120, deadline=aware(2026, 6, 2, 18))
    longe = TarefaFactory(esforco_estimado=120, deadline=aware(2026, 6, 5, 18))
    validas, _ = P.validar_tarefas([perto.id, longe.id])
    base = P.montar_plano(validas, SEG, {})

    res = P.montar_plano(validas, SEG, {}, {"excluir_tarefas": [str(longe.id)]})
    assert {s.tarefa_id for s in res.sessoes} == {str(perto.id)}
    assert {te.id for te in res.tarefas} == {str(perto.id)}
    # Horizonte do conjunto completo: métricas comparáveis com o base.
    assert res.horizonte_fim == base.horizonte_fim
    assert res.prefs_usadas["excluir_tarefas"] == [str(longe.id)]


@pytest.mark.django_db
def test_montar_plano_excluir_todas_ignora_a_exclusao():
    t = TarefaFactory(esforco_estimado=60, deadline=aware(2026, 6, 2, 18))
    validas, _ = P.validar_tarefas([t.id])
    res = P.montar_plano(validas, SEG, {}, {"excluir_tarefas": [str(t.id)]})
    assert res.sessoes  # cinto de segurança: plano nunca esvazia


# --------------------------------------------------------------------------- #
# Fluxo do endpoint (Celery eager; IA mockada)                                 #
# --------------------------------------------------------------------------- #
def _tarefas_validas():
    return [
        TarefaFactory(esforco_estimado=240, deadline=aware(2026, 6, 2, 18)),
        TarefaFactory(esforco_estimado=120, deadline=aware(2026, 6, 5, 18)),
    ]


def _gerar_lote(api, tarefas):
    """Gera um lote sem IA (determinístico) e devolve (job_id, resultado)."""
    resp = api.post(
        "/api/v1/planejamento/cenarios",
        {"tarefa_ids": [str(t.id) for t in tarefas], "a_partir_de": SEG.isoformat()},
        format="json",
    )
    job_id = resp.data["job_id"]
    status = api.get(f"/api/v1/planejamento/cenarios/{job_id}")
    return job_id, status.data["resultado"]


def _refinar(api, job_id, mensagem="sem academia essa semana", **extra):
    return api.post(
        "/api/v1/planejamento/cenarios/refinar",
        {"job_id": job_id, "mensagem": mensagem, **extra},
        format="json",
    )


_BRUTO_IA = {
    "resposta": "Tirei a academia desta semana e mantive o resto do cenário.",
    "nome": "Base — sem academia",
    "intencao": "O plano de referência sem a tarefa de academia.",
    "diretrizes": {},
}


@pytest.mark.django_db
def test_refinar_adiciona_cenario_ao_lote_e_guarda_conversa(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    job_id, resultado = _gerar_lote(api, tarefas)
    assert resultado["entrada"]["tarefa_ids"] == [str(t.id) for t in tarefas]
    n_antes = len(resultado["cenarios"])

    excluida = str(tarefas[1].id)
    bruto = {**_BRUTO_IA, "diretrizes": {"excluir_tarefas": [excluida]}}
    settings.IA_PLANEJAMENTO_ENABLED = True
    with mock.patch(
        "planner.services.cenarios.refinar_cenario_ia", return_value=bruto
    ) as ia:
        resp = _refinar(api, job_id, cenario_id="base")
    assert resp.status_code == 202
    assert resp.data["tempo_estimado_s"] > 0

    status = api.get(f"/api/v1/planejamento/cenarios/refinar/{resp.data['job_id']}")
    assert status.data["status"] == "pronto"
    refino = status.data["resultado"]
    assert refino["ia_indisponivel"] is False
    assert refino["resposta"] == _BRUTO_IA["resposta"]

    novo = refino["cenario"]
    assert novo["origem"] == "base"
    assert novo["diretrizes"]["excluir_tarefas"] == [excluida]
    assert excluida not in {s["tarefa_id"] for s in novo["plano"]["sessoes"]}
    assert novo["sugerido"] is False

    # O cenário novo entrou no lote original (mesmo job_id do escolher).
    lote = api.get(f"/api/v1/planejamento/cenarios/{job_id}").data["resultado"]
    assert len(lote["cenarios"]) == n_antes + 1
    assert novo["id"] in {c["id"] for c in lote["cenarios"]}

    # A IA recebeu o lote e o cenário em foco no contexto...
    contexto = ia.call_args.args[0]
    assert contexto["cenario_em_foco"] == "base"
    assert {c["id"] for c in contexto["cenarios"]} >= {"base"}
    # ...e a conversa ficou guardada para o próximo turno.
    conversa = django_cache.get(f"cenarios_conversa:{job_id}")
    assert [m["role"] for m in conversa] == ["user", "assistant"]

    with mock.patch(
        "planner.services.cenarios.refinar_cenario_ia", return_value=bruto
    ) as ia2:
        _refinar(api, job_id, mensagem="agora também sem o sábado")
    assert ia2.call_args.args[1] == conversa  # histórico reenviado


@pytest.mark.django_db
def test_refinar_com_ollama_fora_nao_mexe_no_lote(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    job_id, resultado = _gerar_lote(api, tarefas)
    n_antes = len(resultado["cenarios"])

    settings.IA_PLANEJAMENTO_ENABLED = True
    with mock.patch(
        "planner.services.cenarios.refinar_cenario_ia",
        side_effect=OllamaIndisponivel("down"),
    ):
        resp = _refinar(api, job_id)
    status = api.get(f"/api/v1/planejamento/cenarios/refinar/{resp.data['job_id']}")
    refino = status.data["resultado"]
    assert refino["ia_indisponivel"] is True
    assert refino["cenario"] is None

    lote = api.get(f"/api/v1/planejamento/cenarios/{job_id}").data["resultado"]
    assert len(lote["cenarios"]) == n_antes
    assert django_cache.get(f"cenarios_conversa:{job_id}") is None


@pytest.mark.django_db
def test_escolher_funciona_no_cenario_refinado(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    job_id, _ = _gerar_lote(api, tarefas)

    bruto = {**_BRUTO_IA, "diretrizes": {"excluir_tarefas": [str(tarefas[1].id)]}}
    settings.IA_PLANEJAMENTO_ENABLED = True
    with mock.patch("planner.services.cenarios.refinar_cenario_ia", return_value=bruto):
        resp = _refinar(api, job_id, cenario_id="base")
    refino_id = resp.data["job_id"]
    novo = api.get(f"/api/v1/planejamento/cenarios/refinar/{refino_id}").data[
        "resultado"
    ]["cenario"]

    resp = api.post(
        "/api/v1/planejamento/cenarios/escolher",
        {"job_id": job_id, "cenario_id": novo["id"], "aplicar": False},
        format="json",
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_refinar_valida_job_cenario_e_lote_antigo(api, eager, settings):
    resp = _refinar(api, "nao-existe")
    assert resp.status_code == 404

    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    job_id, _ = _gerar_lote(api, tarefas)
    resp = _refinar(api, job_id, cenario_id="nao-existe")
    assert resp.status_code == 400

    resp = _refinar(api, job_id, mensagem="")
    assert resp.status_code == 400

    # Lote gerado antes do C5 (sem `entrada`) não é refinável.
    django_cache.set("cenarios_job:legado", {"cenarios": [{"id": "base"}]}, 60)
    resp = _refinar(api, "legado")
    assert resp.status_code == 409
