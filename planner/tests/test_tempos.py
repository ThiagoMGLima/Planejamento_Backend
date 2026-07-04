"""Testes da estimativa adaptativa de duração (Marco C6) — services/tempos.

Cobre a EWMA da razão real/prevista, a semente FATOR_CENARIOS no endpoint de
cenários e a calibração de ponta a ponta: depois de um job real mais lento que
a fórmula, a PRÓXIMA estimativa sobe.
"""

from unittest import mock

import pytest
from django.core.cache import cache as django_cache
from rest_framework.test import APIClient

from planner.services import tempos

from .factories import TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "tempos-tests",
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
# Unidade: EWMA da razão                                                       #
# --------------------------------------------------------------------------- #
def test_estimar_sem_historico_devolve_a_formula():
    assert tempos.estimar("x", 61) == 61


def test_registrar_aplica_a_razao_na_proxima_estimativa():
    # Job real levou 300s onde a fórmula previa 60 → razão 5.
    tempos.registrar("x", 300, 60)
    assert tempos.estimar("x", 60) == 300
    # A razão é multiplicativa: escala com a fórmula (outro nº de tarefas).
    assert tempos.estimar("x", 120) == 600


def test_ewma_converge_sem_saltar():
    tempos.registrar("x", 300, 60)  # razão 5
    razao = tempos.registrar("x", 60, 60)  # observação nova razão 1
    assert razao == pytest.approx(0.6 * 5 + 0.4 * 1)
    assert tempos.estimar("x", 60) == round(60 * razao)


def test_estimar_nunca_devolve_menos_que_1s():
    tempos.registrar("x", 0, 1000)
    assert tempos.estimar("x", 1000) == 1


def test_familias_sao_independentes():
    tempos.registrar("a", 300, 60)
    assert tempos.estimar("b", 60) == 60


# --------------------------------------------------------------------------- #
# Fiação: endpoints usam a semente e aprendem com o job real                   #
# --------------------------------------------------------------------------- #
def _tarefas_validas():
    return [
        TarefaFactory(esforco_estimado=240, deadline=aware(2026, 6, 2, 18)),
        TarefaFactory(esforco_estimado=120, deadline=aware(2026, 6, 5, 18)),
    ]


def _post_cenarios(api, tarefas):
    return api.post(
        "/api/v1/planejamento/cenarios",
        {"tarefa_ids": [str(t.id) for t in tarefas], "a_partir_de": SEG.isoformat()},
        format="json",
    )


@pytest.mark.django_db
def test_cenarios_aprende_com_a_duracao_real_do_job(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = True
    tarefas = _tarefas_validas()

    candidatos = [
        {"nome": "Leve", "intencao": "x", "diretrizes": {"max_min_por_dia_total": 90}}
    ] * 3
    # IA "demorou" 500s. O mock do time é escopado ao módulo tasks (o nome
    # `time` dentro dele), para não afetar o relógio do Celery/Django.
    with (
        mock.patch(
            "planner.services.cenarios.gerar_cenarios_ia", return_value=candidatos
        ),
        mock.patch("planner.tasks.time") as mtime,
    ):
        mtime.monotonic.side_effect = [0.0, 500.0]
        resp = _post_cenarios(api, tarefas)
    assert resp.status_code == 202

    # As 2 tarefas entram no plano base → fórmula = base + 2×por_tarefa;
    # semente da família = FATOR_CENARIOS × fórmula.
    prevista = tempos.FATOR_CENARIOS * (
        settings.PLANEJAR_TEMPO_BASE_S + 2 * settings.PLANEJAR_TEMPO_POR_TAREFA_S
    )
    # Em eager a task roda dentro do .delay(): a razão (500/prevista) já foi
    # registrada e a estimativa passa a refletir a duração real.
    assert django_cache.get("tempo_razao:cenarios") == pytest.approx(500 / prevista)
    assert resp.data["tempo_estimado_s"] == 500
    assert tempos.estimar("cenarios", prevista) == 500


@pytest.mark.django_db
def test_degradacao_sem_ia_nao_calibra(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    _post_cenarios(api, _tarefas_validas())
    assert django_cache.get("tempo_razao:cenarios") is None


@pytest.mark.django_db
def test_estimativa_planejar_ia_usa_razao_aprendida(api):
    tarefas = _tarefas_validas()
    ids = "&".join(f"tarefa_ids={t.id}" for t in tarefas)
    antes = api.get(f"/api/v1/planejamento/planejar-ia/estimativa?{ids}")

    tempos.registrar(
        "planejar_ia",
        antes.data["tempo_estimado_s"] * 2,
        antes.data["tempo_estimado_s"],
    )
    depois = api.get(f"/api/v1/planejamento/planejar-ia/estimativa?{ids}")
    assert depois.data["tempo_estimado_s"] == antes.data["tempo_estimado_s"] * 2


@pytest.mark.django_db
def test_refino_usa_razao_aprendida(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    resp = _post_cenarios(api, tarefas)
    job_id = resp.data["job_id"]
    api.get(f"/api/v1/planejamento/cenarios/{job_id}")

    tempos.registrar("refino", 120, 60)  # razão 2
    resp = api.post(
        "/api/v1/planejamento/cenarios/refinar",
        {"job_id": job_id, "mensagem": "sem academia"},
        format="json",
    )
    assert resp.status_code == 202
    formula = settings.PLANEJAR_TEMPO_BASE_S + settings.PLANEJAR_TEMPO_POR_TAREFA_S * 2
    assert resp.data["tempo_estimado_s"] == formula * 2
