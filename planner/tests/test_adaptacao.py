"""Testes do registro de execução + fatores adaptativos (Marco C3).

Cobre: escrita do RegistroExecucao por concluir/remarcar (com e sem real_min),
o fator de estimativa por classe (EWMA, clamp, mínimo de amostras), a
flexibilidade por classe, o decaimento dos pesos e a integração com o solver
(esforço efetivo + echo) e com o contexto da IA.
"""

import pytest
from rest_framework.test import APIClient

from planner.models import Evento, PesoPreferencia, RegistroExecucao
from planner.services import adaptacao, completion
from planner.services import planejamento as P
from planner.services import planejamento_ia as IA

from .factories import ClasseFactory, EventoFactory, TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    """Isola o cache (o fator_classe é cacheado com TTL curto)."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "adaptacao-tests",
        }
    }
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


def _evento(classe=None, dur_h=2, **kw):
    return EventoFactory(
        classe=classe or ClasseFactory(),
        inicio=aware(2026, 6, 1, 8),
        fim=aware(2026, 6, 1, 8 + dur_h),
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        **kw,
    )


def _registro(classe, planejado=60, real=None, remarcado=False):
    return RegistroExecucao.objects.create(
        classe=classe, planejado_min=planejado, real_min=real, remarcado=remarcado
    )


# --------------------------------------------------------------------------- #
# Escrita pelos fluxos concluir/remarcar                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_concluir_sem_real_min_grava_registro():
    ev = _evento(dur_h=2)
    completion.concluir(ev)
    r = RegistroExecucao.objects.get()
    assert r.evento_id == ev.id
    assert r.classe_id == ev.classe_id
    assert r.planejado_min == 120
    assert r.real_min is None
    assert r.remarcado is False
    assert r.concluido_em is not None


@pytest.mark.django_db
def test_concluir_com_real_min_via_endpoint(api):
    ev = _evento(dur_h=2)
    resp = api.post(
        f"/api/v1/eventos/{ev.id}/concluir/", {"real_min": 90}, format="json"
    )
    assert resp.status_code == 200
    assert RegistroExecucao.objects.get().real_min == 90


@pytest.mark.django_db
def test_concluir_real_min_invalido_400(api):
    ev = _evento()
    for ruim in ("x", 0, -5):
        resp = api.post(
            f"/api/v1/eventos/{ev.id}/concluir/", {"real_min": ruim}, format="json"
        )
        assert resp.status_code == 400
    assert RegistroExecucao.objects.count() == 0


@pytest.mark.django_db
def test_remarcar_grava_registro_remarcado():
    tarefa = TarefaFactory()
    ev = _evento(origem_tarefa=tarefa)
    completion.remarcar(ev)
    r = RegistroExecucao.objects.get()
    assert r.remarcado is True
    assert r.concluido_em is None
    assert r.tarefa_id == tarefa.id


# --------------------------------------------------------------------------- #
# Fator de estimativa por classe (EWMA)                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_fator_neutro_com_poucas_amostras():
    classe = ClasseFactory()
    _registro(classe, 60, 78)
    _registro(classe, 60, 78)
    assert adaptacao.fator_classe(str(classe.id)) == 1.0  # < 3 amostras


@pytest.mark.django_db
def test_fator_converge_para_a_razao_real():
    classe = ClasseFactory()
    for _ in range(10):
        _registro(classe, 60, 78)  # razão 1.3
    esperado = 1.0
    for _ in range(10):
        esperado = 0.7 * esperado + 0.3 * 1.3
    assert adaptacao.fator_classe(str(classe.id)) == round(esperado, 2)
    assert adaptacao.fator_classe(str(classe.id)) > 1.25  # convergiu p/ ~1.3


@pytest.mark.django_db
def test_fator_respeita_clamp():
    lento = ClasseFactory()
    for _ in range(20):
        _registro(lento, 10, 300)  # razão 30 → clampa em 3.0
    assert adaptacao.fator_classe(str(lento.id)) == adaptacao.FATOR_MAXIMO

    rapido = ClasseFactory()
    for _ in range(20):
        _registro(rapido, 300, 10)  # razão ~0.03 → clampa em 0.5
    assert adaptacao.fator_classe(str(rapido.id)) == adaptacao.FATOR_MINIMO


@pytest.mark.django_db
def test_fator_ignora_registros_sem_real_min_e_ids_invalidos():
    classe = ClasseFactory()
    for _ in range(5):
        _registro(classe, 60, None)  # sem real_min: vale só p/ flexibilidade
    assert adaptacao.fator_classe(str(classe.id)) == 1.0
    assert adaptacao.fator_classe("c1") == 1.0  # id hipotético (não-UUID)
    assert adaptacao.fator_classe(None) == 1.0


# --------------------------------------------------------------------------- #
# Flexibilidade por classe                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_flexibilidade_e_a_taxa_de_remarcacao():
    classe = ClasseFactory()
    _registro(classe, remarcado=True)
    _registro(classe, remarcado=True)
    _registro(classe, remarcado=False)
    _registro(classe, remarcado=False)
    assert adaptacao.flexibilidade_classe(str(classe.id)) == 0.5
    assert adaptacao.flexibilidade_classe(str(ClasseFactory().id)) == 0.0


# --------------------------------------------------------------------------- #
# Decaimento dos pesos                                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_decaimento_move_pesos_rumo_ao_neutro():
    PesoPreferencia.objects.create(metrica="fds_livres", valor=2.0)
    PesoPreferencia.objects.create(metrica="pico_min_dia", valor=0.5)
    novos = adaptacao.decair_pesos()
    assert novos["fds_livres"] == pytest.approx(2.0 + 0.02 * (1.0 - 2.0))
    assert novos["pico_min_dia"] == pytest.approx(0.5 + 0.02 * (1.0 - 0.5))
    assert novos["dias_livres"] == 1.0  # neutro fica neutro (e não grava linha)
    assert not PesoPreferencia.objects.filter(metrica="dias_livres").exists()


# --------------------------------------------------------------------------- #
# Integração: solver e contexto                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_solver_aloca_a_mais_com_fator_e_expoe_no_echo():
    classe = ClasseFactory()
    for _ in range(20):
        _registro(classe, 60, 78)  # fator → 1.3
    fator = adaptacao.fator_classe(str(classe.id))
    tarefa = TarefaFactory(
        classe=classe, esforco_estimado=100, deadline=aware(2026, 6, 5, 18)
    )

    res = P.montar_plano([tarefa], SEG, {})
    assert sum(s.dur_min for s in res.sessoes) == round(100 * fator)
    assert res.prefs_usadas["fatores_classe"] == {str(classe.id): fator}

    # usar_fatores=False (replanejar): esforço literal e echo limpo.
    res = P.montar_plano([tarefa], SEG, {}, usar_fatores=False)
    assert sum(s.dur_min for s in res.sessoes) == 100
    assert "fatores_classe" not in res.prefs_usadas


@pytest.mark.django_db
def test_contexto_contem_fatos_adaptativos():
    classe = ClasseFactory()
    for _ in range(4):
        _registro(classe, 60, 90, remarcado=False)
    _registro(classe, remarcado=True)
    tarefa = TarefaFactory(
        classe=classe, esforco_estimado=60, deadline=aware(2026, 6, 5, 18)
    )
    res = P.montar_plano([tarefa], SEG, {})
    ctx = IA.construir_contexto(res)

    cid = str(classe.id)
    assert ctx["fatores_classe"][cid] == adaptacao.fator_classe(cid)
    assert ctx["flexibilidade_classe"][cid] == adaptacao.flexibilidade_classe(cid)
    assert set(ctx["pesos_preferencia"]) == set(adaptacao.METRICAS)
