"""Vocabulário e camada de texto livre (Fase 1.1, PR C).

Dois blocos:

1. **A tabela de tradução** (`services/vocabulario`) — knob interno vira frase
   em português, escrita por CÓDIGO. O modelo escolhe o quê dizer; o texto nunca
   passa por ele.
2. **A guarda de linguagem** — nenhum texto voltado ao usuário pode conter UUID,
   nome de campo ou nome de ferramenta. É teste, não convenção: o prompt do
   planejador já proibia isso em maiúsculas e o agente vazou `classe_id:
   c9a351f9-…` mesmo assim (dogfooding de 15/08/2026). Princípio 9 do ROADMAP —
   nunca abaixo de "falha no teste".
"""

import re
from datetime import date

import pytest

from planner.services import planejamento_ia, vocabulario

# --------------------------------------------------------------------------- #
# A guarda de linguagem                                                        #
# --------------------------------------------------------------------------- #
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

TERMOS_TECNICOS = (
    "classe_id",
    "tarefa_id",
    "evento_id",
    "estrategia",
    "buffer_dias",
    "max_min_por_dia",
    "nao_antes_de",
    "nao_depois_de",
    "janela_inicio",
    "janela_fim",
    "dias_permitidos",
    "esforco_min",
    "a_partir_de",
    "aplicar_plano",
    "simular_plano",
    "criar_tarefa",
    "consultar_agenda",
    "nao_alocado",
    "UUID",
)


def assert_sem_jargao(texto):
    """Falha se `texto` (voltado ao usuário) tiver id ou nome de campo."""
    assert not UUID_RE.search(texto), f"UUID vazou: {texto!r}"
    baixo = texto.lower()
    for termo in TERMOS_TECNICOS:
        assert termo.lower() not in baixo, f"termo técnico {termo!r} em {texto!r}"


def test_a_guarda_pega_uuid_e_nome_de_campo():
    """A guarda tem de falhar de verdade — senão passa a aprovar tudo.

    O caso é literal: foi o que o agente respondeu ao usuário em 15/08/2026.
    """
    with pytest.raises(AssertionError):
        assert_sem_jargao(
            "Vamos começar pelo Estudar "
            "(classe_id: c9a351f9-7ad7-4c27-9676-dbb73d627e13)"
        )
    with pytest.raises(AssertionError):
        assert_sem_jargao("Ajustei o buffer_dias para 2.")
    assert_sem_jargao("Estuda perto do prazo, só de manhã.")  # não levanta


# --------------------------------------------------------------------------- #
# A tabela                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "knob,valor,esperado",
    [
        ("estrategia", "TARDE", "estuda perto do prazo"),
        ("estrategia", "CEDO", "começa o quanto antes"),
        ("nao_antes_de", "2026-10-19", "não começa antes de 19/10"),
        ("nao_depois_de", date(2026, 10, 20), "termina até 20/10"),
        ("janela", (8 * 60, 12 * 60), "só de manhã"),
        ("janela", (13 * 60, 18 * 60), "só à tarde"),
        ("janela", (19 * 60, 22 * 60), "só à noite"),
        ("janela", (10 * 60, 20 * 60), "só entre 10:00 e 20:00"),
        ("dias_permitidos", [0], "só às segundas"),
        ("dias_permitidos", [0, 2], "só às segundas e quartas"),
        ("dias_permitidos", [0, 2, 4], "só às segundas, quartas e sextas"),
        ("buffer_dias", 1, "termina na véspera"),
        ("buffer_dias", 3, "termina 3 dias antes"),
        ("max_min_por_dia", 90, "no máximo 90 min por dia"),
        ("prioridade", 5, "prioridade máxima"),
    ],
)
def test_frases(knob, valor, esperado):
    assert vocabulario.frase(knob, valor) == esperado


@pytest.mark.parametrize(
    "knob,valor",
    [
        ("estrategia", "ASAP"),  # valor desconhecido
        ("prioridade", 3),  # neutro: não vale dizer nada
        ("buffer_dias", 0),
        ("dias_permitidos", []),
        ("dias_permitidos", [0, 1, 2, 3, 4, 5, 6]),  # não restringe nada
        ("knob_que_nao_existe", "x"),
        ("nao_antes_de", "não é data"),
    ],
)
def test_frase_ausente_devolve_none(knob, valor):
    """Camada cosmética nunca levanta — no máximo cala a boca."""
    assert vocabulario.frase(knob, valor) is None


def test_toda_frase_passa_na_guarda_de_linguagem():
    """O ponto do módulo: nada que ele produz pode conter jargão."""
    casos = [
        ("estrategia", "TARDE"),
        ("estrategia", "CEDO"),
        ("nao_antes_de", "2026-10-19"),
        ("nao_depois_de", "2026-10-20"),
        ("janela", (8 * 60, 12 * 60)),
        ("janela", (9 * 60, 21 * 60)),
        ("dias_permitidos", [0, 3]),
        ("buffer_dias", 2),
        ("max_min_por_dia", 60),
        ("prioridade", 5),
        ("prioridade", 1),
    ]
    for knob, valor in casos:
        texto = vocabulario.frase(knob, valor)
        assert texto
        assert_sem_jargao(texto)


def test_descrever_junta_a_janela_numa_frase_so():
    ajuste = {
        "estrategia": "TARDE",
        "janela_inicio": "08:00",
        "janela_fim": "12:00",
        "max_min_por_dia": 60,
    }
    assert vocabulario.descrever(ajuste) == [
        "estuda perto do prazo",
        "só de manhã",
        "no máximo 60 min por dia",
    ]


def test_descrever_ignora_lixo():
    assert vocabulario.descrever(None) == []
    assert vocabulario.descrever({}) == []
    assert vocabulario.descrever({"janela_inicio": "08:00"}) == []  # sem o par


def test_pergunta_usa_o_titulo_e_a_frase():
    texto = vocabulario.pergunta("Estudar para a PP1", "estrategia", "TARDE")
    assert texto == "Quer que «Estudar para a PP1» estuda perto do prazo?"
    assert_sem_jargao(texto)


def test_pergunta_sem_frase_e_none():
    assert vocabulario.pergunta("X", "prioridade", 3) is None


# --------------------------------------------------------------------------- #
# Prompts: a regra de linguagem existe nos DOIS                                #
# --------------------------------------------------------------------------- #
def test_os_dois_prompts_proibem_id_e_nome_de_campo():
    """O do planejador já tinha; o do agente não — e foi por onde vazou."""
    from planner.services import agente

    for prompt in (planejamento_ia.SYSTEM_PROMPT, agente.SYSTEM_PROMPT):
        baixo = prompt.lower()
        assert "nunca" in baixo
        assert "uuid" in baixo  # proibição explícita de id
        assert "campo" in baixo  # proibição explícita de nome de campo
