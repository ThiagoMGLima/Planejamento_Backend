"""Testes do planejador de produção multitarefa (services/planejamento).

Cobre o algoritmo guloso EDF: soma exata, ordenação por deadline, tetos diários,
anti-conflito (com eventos existentes e entre sessões), a cascata de relaxamento
(fim de semana → tetos → janela 24h → sessões curtas) e o não-alocado.

A maioria é teste puro (calcular_plano não toca no banco); só
`intervalos_ocupados` precisa de DB.
"""

from datetime import timedelta

import pytest

from planner.services import planejamento as P

from .factories import EventoFactory, RegraRecorrenciaFactory, aware

# 2026-06-01 é uma segunda-feira (mesma âncora dos demais testes).
SEG = aware(2026, 6, 1, 8)


def _tarefa(id, esforco, deadline, classe_id="c1", titulo=None, **knobs):
    return P.TarefaEntrada(
        id=id,
        titulo=titulo or f"Tarefa {id}",
        classe_id=classe_id,
        esforco=esforco,
        deadline=deadline,
        **knobs,  # prioridade / buffer_dias / max_min_por_dia (Fase A)
    )


def _sem_sobreposicao(sessoes):
    iv = sorted((s.inicio, s.fim) for s in sessoes)
    return all(iv[i][1] <= iv[i + 1][0] for i in range(len(iv) - 1))


def _total(sessoes, tarefa_id):
    return sum(s.dur_min for s in sessoes if s.tarefa_id == tarefa_id)


# --------------------------------------------------------------------------- #
# Preferências                                                                 #
# --------------------------------------------------------------------------- #
def test_montar_preferencias_aplica_defaults():
    prefs, usadas = P.montar_preferencias({})
    assert usadas == P.DEFAULTS
    assert prefs.janela_inicio_min == 8 * 60
    assert prefs.janela_fim_min == 22 * 60
    assert prefs.max_min_por_dia_por_tarefa == 120
    assert prefs.max_min_por_dia_total is None


def test_montar_preferencias_respeita_null_explicito():
    prefs, usadas = P.montar_preferencias({"max_min_por_dia_por_tarefa": None})
    assert prefs.max_min_por_dia_por_tarefa is None
    assert usadas["max_min_por_dia_por_tarefa"] is None


def test_montar_preferencias_sobrescreve_parcial():
    prefs, usadas = P.montar_preferencias({"janela_inicio": "06:00", "sessao_max": 60})
    assert prefs.janela_inicio_min == 6 * 60
    assert prefs.sessao_max == 60
    assert prefs.janela_fim_min == 22 * 60  # default mantido


# --------------------------------------------------------------------------- #
# Núcleo guloso                                                                #
# --------------------------------------------------------------------------- #
def test_soma_das_sessoes_igual_ao_esforco():
    prefs, _ = P.montar_preferencias({})
    t = _tarefa("A", 300, SEG + timedelta(days=5))
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    assert _total(sessoes, "A") == 300
    assert nao == []
    assert _sem_sobreposicao(sessoes)


def test_sessoes_dentro_da_janela_e_antes_da_deadline():
    prefs, _ = P.montar_preferencias({})
    t = _tarefa("A", 240, SEG + timedelta(days=5))
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    for s in sessoes:
        assert 8 <= s.inicio.hour
        assert s.fim.hour <= 22 or (s.fim.hour == 22 and s.fim.minute == 0)
        assert s.fim <= t.deadline


def test_ordena_por_edf_deadline_mais_curta_primeiro():
    prefs, _ = P.montar_preferencias({})
    cedo = _tarefa("CEDO", 120, SEG + timedelta(days=2))
    tarde = _tarefa("TARDE", 120, SEG + timedelta(days=5))
    sessoes, _ = P.calcular_plano([tarde, cedo], [], prefs, SEG, tarde.deadline)
    # A sessão mais cedo no tempo pertence à tarefa de deadline mais curta.
    primeira = min(sessoes, key=lambda s: s.inicio)
    assert primeira.tarefa_id == "CEDO"


def test_respeita_teto_por_dia_por_tarefa():
    prefs, _ = P.montar_preferencias({})  # 120 min/dia/tarefa
    t = _tarefa("A", 300, SEG + timedelta(days=10))
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    por_dia = {}
    for s in sessoes:
        por_dia[s.inicio.date()] = por_dia.get(s.inicio.date(), 0) + s.dur_min
    assert all(total <= 120 for total in por_dia.values())


def test_evita_fim_de_semana_quando_cabe_em_dias_uteis():
    prefs, _ = P.montar_preferencias({})
    # 240 min cabem em seg+ter (120/dia) antes de sexta.
    t = _tarefa("A", 240, aware(2026, 6, 5, 18))
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    assert nao == []
    assert all(s.inicio.weekday() < 5 for s in sessoes)


def test_relaxa_para_fim_de_semana_quando_nao_cabe():
    prefs, _ = P.montar_preferencias({})
    # Sexta 05/06 08:00 → deadline seg 08/06 08:00. 240 min não cabem só na
    # sexta (teto 120/dia), então o relaxamento usa o fim de semana.
    sexta = aware(2026, 6, 5, 8)
    t = _tarefa("A", 240, aware(2026, 6, 8, 8))
    sessoes, nao = P.calcular_plano([t], [], prefs, sexta, t.deadline)
    assert _total(sessoes, "A") == 240
    assert any(s.inicio.weekday() >= 5 for s in sessoes)


def test_relaxa_para_janela_24h_quando_dias_estao_lotados():
    prefs, _ = P.montar_preferencias({})
    # Janela diurna (08-22) lotada na seg e ter; deadline qua 08:00.
    # Só sobra a madrugada → relaxa a janela para o dia inteiro.
    ocupado = [
        (aware(2026, 6, 1, 8), aware(2026, 6, 1, 22)),
        (aware(2026, 6, 2, 8), aware(2026, 6, 2, 22)),
    ]
    t = _tarefa("A", 120, aware(2026, 6, 3, 8))
    sessoes, nao = P.calcular_plano([t], ocupado, prefs, SEG, t.deadline)
    assert _total(sessoes, "A") == 120
    assert nao == []
    # Alguma sessão fora da janela 08-22.
    assert any(s.inicio.hour >= 22 or s.inicio.hour < 8 for s in sessoes)


def test_evita_conflito_com_intervalo_ocupado():
    prefs, _ = P.montar_preferencias({"max_min_por_dia_por_tarefa": None})
    ocupado = [(aware(2026, 6, 1, 8), aware(2026, 6, 1, 12))]
    t = _tarefa("A", 120, aware(2026, 6, 2, 8))
    sessoes, _ = P.calcular_plano([t], ocupado, prefs, SEG, t.deadline)
    assert all(s.inicio >= aware(2026, 6, 1, 12) for s in sessoes)
    assert _sem_sobreposicao(sessoes)


def test_sem_sobreposicao_entre_tarefas_distintas():
    prefs, _ = P.montar_preferencias({})
    a = _tarefa("A", 300, SEG + timedelta(days=6))
    b = _tarefa("B", 300, SEG + timedelta(days=6))
    sessoes, _ = P.calcular_plano([a, b], [], prefs, SEG, a.deadline)
    assert _sem_sobreposicao(sessoes)


def test_deadline_no_passado_vai_para_nao_alocado():
    prefs, _ = P.montar_preferencias({})
    t = _tarefa("A", 60, SEG - timedelta(days=1))
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, SEG + timedelta(days=2))
    assert sessoes == []
    assert len(nao) == 1
    assert nao[0].tarefa_id == "A"
    assert nao[0].minutos_restantes == 60
    assert nao[0].motivo == "deadline no passado"


def test_o_que_nao_cabe_vira_nao_alocado_com_restante():
    prefs, _ = P.montar_preferencias({})
    # Horizonte curtíssimo (1 dia) para um esforço grande, mesmo após relaxar.
    t = _tarefa("A", 5000, SEG + timedelta(hours=10))
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    alocado = _total(sessoes, "A")
    assert len(nao) == 1
    assert nao[0].minutos_restantes == 5000 - alocado
    assert nao[0].minutos_restantes > 0
    assert nao[0].motivo == "sem espaço livre antes da deadline"


# --------------------------------------------------------------------------- #
# Knobs por tarefa (Fase A): prioridade, buffer_dias, max_min_por_dia          #
# --------------------------------------------------------------------------- #
def test_prioridade_desempata_so_em_empate_de_deadline():
    prefs, _ = P.montar_preferencias({})
    deadline = SEG + timedelta(days=5)
    # Mesma deadline: a de maior prioridade pega o slot mais cedo.
    baixa = _tarefa("BAIXA", 120, deadline, prioridade=1)
    alta = _tarefa("ALTA", 120, deadline, prioridade=5)
    sessoes, _ = P.calcular_plano([baixa, alta], [], prefs, SEG, deadline)
    primeira = min(sessoes, key=lambda s: s.inicio)
    assert primeira.tarefa_id == "ALTA"


def test_prioridade_nao_supera_deadline():
    prefs, _ = P.montar_preferencias({})
    # Mesmo com prioridade máxima, a deadline mais curta vem primeiro (EDF manda).
    cedo = _tarefa("CEDO", 120, SEG + timedelta(days=2), prioridade=1)
    tarde = _tarefa("TARDE", 120, SEG + timedelta(days=5), prioridade=5)
    sessoes, _ = P.calcular_plano([tarde, cedo], [], prefs, SEG, tarde.deadline)
    primeira = min(sessoes, key=lambda s: s.inicio)
    assert primeira.tarefa_id == "CEDO"


def test_buffer_dias_antecipa_o_termino():
    prefs, _ = P.montar_preferencias({})
    deadline = SEG + timedelta(days=7)
    t = _tarefa("A", 240, deadline, buffer_dias=2)
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert nao == []
    limite = deadline - timedelta(days=2)
    assert all(s.fim <= limite for s in sessoes)


def test_buffer_impossivel_usa_a_deadline_real():
    prefs, _ = P.montar_preferencias({})
    # buffer jogaria o término pro passado → ignora o buffer, não some a tarefa.
    deadline = SEG + timedelta(days=1)
    t = _tarefa("A", 120, deadline, buffer_dias=5)
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert _total(sessoes, "A") == 120
    assert nao == []
    assert all(s.fim <= deadline for s in sessoes)


def test_max_min_por_dia_da_tarefa_limita_abaixo_do_global():
    prefs, _ = P.montar_preferencias({})  # global 120/dia/tarefa
    t = _tarefa("A", 300, SEG + timedelta(days=10), max_min_por_dia=60)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    por_dia = {}
    for s in sessoes:
        por_dia[s.inicio.date()] = por_dia.get(s.inicio.date(), 0) + s.dur_min
    assert all(total <= 60 for total in por_dia.values())


def test_knobs_preservam_invariantes():
    prefs, _ = P.montar_preferencias({})
    t = _tarefa(
        "A",
        300,
        SEG + timedelta(days=10),
        prioridade=4,
        buffer_dias=1,
        max_min_por_dia=90,
    )
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, t.deadline)
    assert _total(sessoes, "A") == 300
    assert nao == []
    assert _sem_sobreposicao(sessoes)


# --------------------------------------------------------------------------- #
# Eventos ocupados (precisa de DB)                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_intervalos_ocupados_inclui_simples_e_recorrentes():
    # Evento simples na seg 01/06.
    EventoFactory(inicio=aware(2026, 6, 1, 9), fim=aware(2026, 6, 1, 11))
    # Evento recorrente semanal às segundas (01/06 e 08/06 no horizonte).
    regra = RegraRecorrenciaFactory(tipo="SEMANAL", dias=[0])
    EventoFactory(
        inicio=aware(2026, 6, 1, 14),
        fim=aware(2026, 6, 1, 16),
        regra_recorrencia=regra,
    )
    ocupado = P.intervalos_ocupados(aware(2026, 6, 1), aware(2026, 6, 15))
    # 1 simples + 2 ocorrências recorrentes (seg 01 e seg 08).
    assert (aware(2026, 6, 1, 9), aware(2026, 6, 1, 11)) in ocupado
    assert (aware(2026, 6, 1, 14), aware(2026, 6, 1, 16)) in ocupado
    assert (aware(2026, 6, 8, 14), aware(2026, 6, 8, 16)) in ocupado
