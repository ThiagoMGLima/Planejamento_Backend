"""Testes do replanejar a partir de agora (Marco C2) — sem IA, síncrono.

Cobre: passado/concluído congelados, esforço restante devolvido ao pool,
exclusão do auto-bloqueio, "hoje não" (dias_bloqueados), o diff
(movida/criada/removida/inalterada) e o aplicar atômico/idempotente.
"""

from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from planner.models import Evento, Tarefa
from planner.services import planejamento as P
from planner.services import replanejamento as R

from .factories import ClasseFactory, EventoFactory, TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)  # segunda-feira


@pytest.fixture
def api():
    return APIClient()


def _tarefa_promovida(esforco=120, deadline=None, **kw):
    return TarefaFactory(
        esforco_estimado=esforco,
        deadline=deadline or aware(2026, 6, 5, 18),
        status=Tarefa.Status.PROMOVIDA,
        **kw,
    )


def _sessao(tarefa, inicio, fim, status=Evento.Status.AGENDADO):
    return EventoFactory(
        titulo=tarefa.titulo,
        classe=tarefa.classe,
        inicio=inicio,
        fim=fim,
        origem_tarefa=tarefa,
        rastrear_conclusao=True,
        status=status,
    )


# --------------------------------------------------------------------------- #
# Serviço                                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_passado_e_concluido_congelam_so_futuras_substituidas():
    t = _tarefa_promovida(esforco=360)
    passada = _sessao(t, aware(2026, 5, 29, 8), aware(2026, 5, 29, 10))
    concluida = _sessao(
        t, aware(2026, 6, 2, 8), aware(2026, 6, 2, 10), status=Evento.Status.CONCLUIDO
    )
    futura = _sessao(t, aware(2026, 6, 3, 8), aware(2026, 6, 3, 10))

    rp = R.replanejar(agora=SEG)
    ids = {ev.id for ev in rp.substituiveis}
    assert futura.id in ids
    assert passada.id not in ids
    assert concluida.id not in ids


@pytest.mark.django_db
def test_esforco_restante_bate_com_as_sessoes_substituidas():
    t = _tarefa_promovida(esforco=600)  # esforço original NÃO é o que conta
    _sessao(t, aware(2026, 6, 2, 8), aware(2026, 6, 2, 10))  # 120
    _sessao(t, aware(2026, 6, 3, 8), aware(2026, 6, 3, 9, 30))  # 90

    rp = R.replanejar(agora=SEG)
    assert sum(s.dur_min for s in rp.res.sessoes) == 210
    assert rp.res.nao_alocado == []


@pytest.mark.django_db
def test_sessoes_substituidas_nao_se_autobloqueiam():
    # Semana toda ocupada por eventos fixos 08–22, EXCETO o slot da própria
    # sessão futura (ter 08–10): o plano novo dentro da janela só fecha se
    # aquele slot for reutilizável (sem a exclusão, cairia na madrugada).
    classe = ClasseFactory()
    for d in range(1, 6):
        inicio_fixo = 10 if d == 2 else 8
        EventoFactory(
            classe=classe,
            inicio=aware(2026, 6, d, inicio_fixo),
            fim=aware(2026, 6, d, 22),
        )
    t = _tarefa_promovida(esforco=120, deadline=aware(2026, 6, 5, 18))
    _sessao(t, aware(2026, 6, 2, 8), aware(2026, 6, 2, 10))

    rp = R.replanejar(agora=SEG)
    assert rp.res.nao_alocado == []
    assert [(s.inicio, s.fim) for s in rp.res.sessoes] == [
        (aware(2026, 6, 2, 8), aware(2026, 6, 2, 10))  # o mesmo slot, reutilizado
    ]


@pytest.mark.django_db
def test_hoje_nao_esvazia_o_dia_e_o_diff_explica():
    t = _tarefa_promovida(esforco=240, deadline=aware(2026, 6, 5, 18))
    _sessao(t, aware(2026, 6, 1, 9), aware(2026, 6, 1, 11))  # hoje
    _sessao(t, aware(2026, 6, 2, 9), aware(2026, 6, 2, 11))

    rp = R.replanejar(agora=SEG, dias_bloqueados=["2026-06-01"])
    assert all(s.inicio.date().isoformat() != "2026-06-01" for s in rp.res.sessoes)
    assert sum(s.dur_min for s in rp.res.sessoes) == 240
    entrada = rp.diff[str(t.id)]
    assert entrada["movidas"]  # a sessão de hoje foi movida, e o diff mostra


@pytest.mark.django_db
def test_inbox_elegivel_entra_no_pool():
    t = TarefaFactory(esforco_estimado=60, deadline=aware(2026, 6, 3, 18))  # INBOX
    rp = R.replanejar(agora=SEG)
    assert sum(s.dur_min for s in rp.res.sessoes if s.tarefa_id == str(t.id)) == 60


@pytest.mark.django_db
def test_sem_nada_para_replanejar_devolve_vazio():
    rp = R.replanejar(agora=SEG)
    assert rp.res.sessoes == []
    assert rp.diff == {}
    assert rp.substituiveis == []


def test_diff_planos_cobre_todos_os_casos():
    def s(tid, d, h, dur=60):
        ini = aware(2026, 6, d, h)
        return P.Sessao(tid, f"T{tid}", "c1", ini, ini + timedelta(minutes=dur), dur)

    antigas = [s("A", 1, 8), s("A", 2, 8), s("B", 3, 8)]
    novas = [s("A", 1, 8), s("A", 2, 10), s("A", 4, 8), s("C", 5, 8)]
    diff = R.diff_planos(antigas, novas)

    a = diff["A"]
    assert len(a["inalteradas"]) == 1  # 01/06 08:00 idêntica
    assert a["movidas"] == [
        {
            "de": {
                "inicio": aware(2026, 6, 2, 8).isoformat(),
                "fim": aware(2026, 6, 2, 9).isoformat(),
            },
            "para": {
                "inicio": aware(2026, 6, 2, 10).isoformat(),
                "fim": aware(2026, 6, 2, 11).isoformat(),
            },
        }
    ]
    assert len(a["criadas"]) == 1  # 04/06
    assert diff["B"]["removidas"] and not diff["B"]["criadas"]
    assert diff["C"]["criadas"] and not diff["C"]["removidas"]


# --------------------------------------------------------------------------- #
# Aplicar (transação)                                                          #
# --------------------------------------------------------------------------- #
def _agenda(t):
    return sorted((ev.inicio, ev.fim) for ev in Evento.objects.filter(origem_tarefa=t))


@pytest.mark.django_db
def test_aplicar_substitui_futuras_e_preserva_passado():
    t = _tarefa_promovida(esforco=360)
    passada = _sessao(t, aware(2026, 5, 29, 8), aware(2026, 5, 29, 10))
    futura = _sessao(t, aware(2026, 6, 3, 8), aware(2026, 6, 3, 10))

    rp, criados, removidos = R.aplicar_replanejamento(agora=SEG)
    assert removidos == 1
    assert criados == len(rp.res.sessoes) > 0
    assert Evento.objects.filter(id=passada.id).exists()
    assert not Evento.objects.filter(id=futura.id).exists()
    novos = Evento.objects.filter(origem_tarefa=t, inicio__gte=SEG)
    assert all(ev.origem_tarefa_id == t.id for ev in novos)  # origem preservada


@pytest.mark.django_db
def test_aplicar_e_idempotente_para_o_mesmo_estado():
    t = _tarefa_promovida(esforco=240)
    _sessao(t, aware(2026, 6, 2, 8), aware(2026, 6, 2, 10))
    _sessao(t, aware(2026, 6, 3, 8), aware(2026, 6, 3, 10))

    R.aplicar_replanejamento(agora=SEG)
    primeira = _agenda(t)
    rp, criados, removidos = R.aplicar_replanejamento(agora=SEG)
    assert _agenda(t) == primeira  # mesmo estado ⇒ mesma agenda
    assert criados == removidos  # trocou igual por igual


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_endpoint_replanejar_simula_sem_persistir(api):
    t = _tarefa_promovida(esforco=120)
    _sessao(t, aware(2026, 6, 2, 8), aware(2026, 6, 2, 10))
    antes = Evento.objects.count()

    resp = api.post(
        "/api/v1/planejamento/replanejar",
        {"a_partir_de": SEG.isoformat()},
        format="json",
    )
    assert resp.status_code == 200
    assert {"plano", "diff", "metricas", "metricas_vs_anterior"} <= set(resp.data)
    assert Evento.objects.count() == antes  # nada persistido


@pytest.mark.django_db
def test_endpoint_aplicar_persiste_e_reporta(api):
    t = _tarefa_promovida(esforco=120)
    _sessao(t, aware(2026, 6, 2, 8), aware(2026, 6, 2, 10))

    resp = api.post(
        "/api/v1/planejamento/replanejar/aplicar",
        {"a_partir_de": SEG.isoformat(), "dias_bloqueados": ["2026-06-02"]},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["eventos_removidos"] == 1
    assert resp.data["eventos_criados"] >= 1
    assert not Evento.objects.filter(
        origem_tarefa=t, inicio__date="2026-06-02"
    ).exists()


@pytest.mark.django_db
def test_endpoint_replanejar_valida_body(api):
    resp = api.post(
        "/api/v1/planejamento/replanejar",
        {"dias_bloqueados": ["não-é-data"]},
        format="json",
    )
    assert resp.status_code == 400
