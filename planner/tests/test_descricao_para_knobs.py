"""Camada de texto livre: `descricao` → knobs (Fase 1.1, PR C).

O gate decidiu **reusar `descricao`** (campo visível e editável) em vez de criar
um campo oculto, e interpretar o **texto inteiro** sem marcador (D3). Isso só é
aceitável com duas travas, que são o coração deste arquivo:

1. **A leitura é SEMPRE reportada** — inclusive quando nada foi extraído. Sem o
   relato, uma leitura errada viraria mudança silenciosa de agenda.
2. **O campo explícito da tarefa VENCE** a inferência. A camada de texto livre é
   tradutora para a camada estruturada, não canal paralelo: pode acrescentar
   condição (e é reportada), nunca desfazer o que alguém disse explicitamente.

Mais o guarda-corpo dos knobs novos e as perguntas (no máximo 3, por impacto).
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from planner.models import Tarefa
from planner.services import planejamento as P
from planner.services import planejamento_ia as IA

from .factories import TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)
HORIZONTE = aware(2026, 6, 30, 22)


def _te(id="A", titulo="Tarefa A", descricao="", **kw):
    return P.TarefaEntrada(
        id=id,
        titulo=titulo,
        classe_id="c1",
        esforco=120,
        deadline=aware(2026, 6, 19, 13),
        descricao=descricao,
        **kw,
    )


def _validar(ajuste, tarefas=None):
    """Atalho: valida um `ajustes_por_tarefa` de uma tarefa só."""
    tarefas = tarefas or [_te()]
    out = IA.validar_diretrizes(
        {"ajustes_por_tarefa": {"A": ajuste}}, tarefas, SEG, HORIZONTE
    )
    return out["ajustes_por_tarefa"].get("A", {})


# --------------------------------------------------------------------------- #
# Guarda-corpo dos knobs novos                                                 #
# --------------------------------------------------------------------------- #
def test_estrategia_valida_passa():
    assert _validar({"estrategia": "TARDE"}) == {"estrategia": "TARDE"}
    assert _validar({"estrategia": "CEDO"}) == {"estrategia": "CEDO"}


@pytest.mark.parametrize("lixo", ["ASAP", "tarde", 1, None, {"a": 1}])
def test_estrategia_invalida_some(lixo):
    assert "estrategia" not in _validar({"estrategia": lixo})


def test_janela_valida_passa_como_par():
    assert _validar({"janela_inicio": "08:00", "janela_fim": "12:00"}) == {
        "janela_inicio": "08:00",
        "janela_fim": "12:00",
    }


@pytest.mark.parametrize(
    "ajuste",
    [
        {"janela_inicio": "08:00"},  # sem par
        {"janela_inicio": "18:00", "janela_fim": "09:00"},  # invertida
        {"janela_inicio": "09:00", "janela_fim": "09:00"},  # vazia
        {"janela_inicio": "03:00", "janela_fim": "07:00"},  # antes de 05:00
        {"janela_inicio": "8h", "janela_fim": "12h"},  # formato errado
    ],
)
def test_janela_invalida_some_inteira(ajuste):
    limpo = _validar(ajuste)
    assert "janela_inicio" not in limpo and "janela_fim" not in limpo


def test_dias_permitidos_dedup_e_ordena():
    assert _validar({"dias_permitidos": [4, 0, 0, 2]}) == {"dias_permitidos": [0, 2, 4]}


@pytest.mark.parametrize(
    "dias", [[], [0, 1, 2, 3, 4, 5, 6], [9], "segunda", None, [-1]]
)
def test_dias_permitidos_inuteis_ou_invalidos_somem(dias):
    assert "dias_permitidos" not in _validar({"dias_permitidos": dias})


def test_datas_fora_do_horizonte_somem():
    assert _validar({"nao_antes_de": "2027-01-01"}) == {}
    assert _validar({"nao_depois_de": "2020-01-01"}) == {}


def test_par_de_datas_contraditorio_descarta_as_duas():
    """Aceitar o par invertido produziria janela vazia e mataria a tarefa."""
    limpo = _validar({"nao_antes_de": "2026-06-20", "nao_depois_de": "2026-06-10"})
    assert "nao_antes_de" not in limpo and "nao_depois_de" not in limpo


def test_validar_diretrizes_nunca_levanta_com_lixo():
    assert IA.validar_diretrizes(
        {"ajustes_por_tarefa": {"A": "não é dict"}}, [_te()], SEG, HORIZONTE
    ) == {"prioridades": {}, "ajustes_por_tarefa": {}}


# --------------------------------------------------------------------------- #
# Leitura sempre reportada (D3)                                                #
# --------------------------------------------------------------------------- #
def test_leitura_reporta_o_que_foi_extraido():
    tarefas = [_te(descricao="só consigo estudar de manhã")]
    ajustes = {"A": {"janela_inicio": "08:00", "janela_fim": "12:00"}}
    assert IA.leitura_das_descricoes(tarefas, ajustes) == [
        {"tarefa_id": "A", "tarefa": "Tarefa A", "entendi": ["só de manhã"]}
    ]


def test_leitura_reporta_tambem_quando_nao_extraiu_nada():
    """'Li e não tirei nada' é informação — é o que impede a mudança silenciosa."""
    tarefas = [_te(descricao="capítulos 3 e 4 do livro")]
    assert IA.leitura_das_descricoes(tarefas, {}) == [
        {"tarefa_id": "A", "tarefa": "Tarefa A", "entendi": []}
    ]


def test_tarefa_sem_descricao_nao_aparece_na_leitura():
    assert IA.leitura_das_descricoes([_te(descricao="   ")], {}) == []


def test_leitura_nao_vaza_jargao():
    from .test_vocabulario import assert_sem_jargao

    tarefas = [_te(descricao="de manhã, perto da prova")]
    ajustes = {
        "A": {
            "estrategia": "TARDE",
            "janela_inicio": "08:00",
            "janela_fim": "12:00",
            "nao_antes_de": "2026-06-15",
        }
    }
    for item in IA.leitura_das_descricoes(tarefas, ajustes):
        for frase in item["entendi"]:
            assert_sem_jargao(frase)


# --------------------------------------------------------------------------- #
# Perguntas: no máximo 3, por impacto, texto do código (D5)                    #
# --------------------------------------------------------------------------- #
def test_perguntas_sao_redigidas_pelo_codigo():
    out = IA.validar_perguntas(
        [{"tarefa_id": "A", "knob": "estrategia", "valor": "TARDE", "impacto": 5}],
        [_te(titulo="Estudar para a PP1")],
        SEG,
        HORIZONTE,
    )
    assert len(out) == 1
    assert out[0]["texto"] == "Quer que «Estudar para a PP1» estuda perto do prazo?"
    assert out[0]["valor"] == "TARDE"  # o que o front devolve se o usuário aceitar


def test_no_maximo_tres_perguntas_por_impacto():
    bruto = [
        {"tarefa_id": "A", "knob": "estrategia", "valor": "TARDE", "impacto": 1},
        {"tarefa_id": "A", "knob": "nao_antes_de", "valor": "2026-06-10", "impacto": 9},
        {
            "tarefa_id": "A",
            "knob": "nao_depois_de",
            "valor": "2026-06-18",
            "impacto": 7,
        },
        {"tarefa_id": "A", "knob": "dias_permitidos", "valor": [0, 2], "impacto": 5},
    ]
    out = IA.validar_perguntas(bruto, [_te()], SEG, HORIZONTE)
    assert [p["impacto"] for p in out] == [9, 7, 5]


def test_pergunta_com_knob_ou_tarefa_desconhecidos_some():
    bruto = [
        {"tarefa_id": "A", "knob": "inventado", "valor": "x", "impacto": 9},
        {"tarefa_id": "ZZZ", "knob": "estrategia", "valor": "TARDE", "impacto": 9},
        {"tarefa_id": "A", "knob": "estrategia", "valor": "ASAP", "impacto": 9},
    ]
    assert IA.validar_perguntas(bruto, [_te()], SEG, HORIZONTE) == []


def test_perguntas_com_lixo_nao_levanta():
    assert IA.validar_perguntas(None, [_te()], SEG, HORIZONTE) == []
    assert IA.validar_perguntas(["texto solto"], [_te()], SEG, HORIZONTE) == []


def test_pergunta_nao_vaza_jargao():
    from .test_vocabulario import assert_sem_jargao

    bruto = [
        {"tarefa_id": "A", "knob": "janela_inicio", "valor": "08:00", "impacto": 3},
        {"tarefa_id": "A", "knob": "estrategia", "valor": "TARDE", "impacto": 9},
        {"tarefa_id": "A", "knob": "dias_permitidos", "valor": [0, 3], "impacto": 5},
    ]
    for p in IA.validar_perguntas(bruto, [_te()], SEG, HORIZONTE):
        assert_sem_jargao(p["texto"])


# --------------------------------------------------------------------------- #
# Precedência: o campo explícito vence a inferência                            #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_diretriz_preenche_o_que_a_tarefa_deixou_nulo(perfil):
    t = TarefaFactory(
        dono=perfil,
        deadline=timezone.now() + timedelta(days=20),
        esforco_estimado=120,
        descricao="só de manhã",
    )
    res = P.montar_plano(
        perfil,
        [t],
        timezone.now(),
        {},
        diretrizes={
            "ajustes_por_tarefa": {
                str(t.id): {"janela_inicio": "08:00", "janela_fim": "12:00"}
            }
        },
    )
    te = res.tarefas[0]
    assert te.janela_inicio_min == 8 * 60
    assert te.janela_fim_min == 12 * 60


@pytest.mark.django_db
def test_campo_explicito_da_tarefa_vence_a_diretriz(perfil):
    """A IA não pode desfazer o que alguém setou de propósito."""
    t = TarefaFactory(
        dono=perfil,
        deadline=timezone.now() + timedelta(days=20),
        esforco_estimado=120,
        estrategia=Tarefa.Estrategia.TARDE,
        nao_antes_de=date(2026, 6, 15),
        descricao="pode ser quando der",
    )
    res = P.montar_plano(
        perfil,
        [t],
        timezone.now(),
        {},
        diretrizes={
            "ajustes_por_tarefa": {
                str(t.id): {"estrategia": "CEDO", "nao_antes_de": "2026-06-01"}
            }
        },
    )
    te = res.tarefas[0]
    assert te.estrategia == "TARDE"
    assert te.nao_antes_de == date(2026, 6, 15)


@pytest.mark.django_db
def test_contexto_leva_a_descricao_e_o_que_ja_esta_definido(perfil):
    t = TarefaFactory(
        dono=perfil,
        deadline=timezone.now() + timedelta(days=20),
        esforco_estimado=120,
        estrategia=Tarefa.Estrategia.TARDE,
        descricao="  só de manhã  ",
    )
    res = P.montar_plano(perfil, [t], timezone.now(), {})
    ctx = IA.construir_contexto(res)
    item = ctx["tarefas"][0]
    assert item["observacao_do_usuario"] == "só de manhã"
    assert item["ja_definido"]["estrategia"] == "TARDE"


@pytest.mark.django_db
def test_contexto_omite_as_chaves_quando_nao_ha_nada(perfil):
    t = TarefaFactory(
        dono=perfil,
        deadline=timezone.now() + timedelta(days=20),
        esforco_estimado=120,
        descricao="",
    )
    res = P.montar_plano(perfil, [t], timezone.now(), {})
    item = IA.construir_contexto(res)["tarefas"][0]
    assert "observacao_do_usuario" not in item
    assert "ja_definido" not in item


# --------------------------------------------------------------------------- #
# criar_tarefa aceita a estratégia (senão a IA cria tarefa sem ela)            #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_agente_cria_tarefa_com_estrategia(perfil):
    from planner.services import agente

    out = agente._criar_tarefa(
        perfil, titulo="Estudar para a P1", estrategia="TARDE", esforco_min=120
    )
    assert out["estrategia"] == "TARDE"
    assert Tarefa.objects.do_dono(perfil).get(
        titulo="Estudar para a P1"
    ).estrategia == ("TARDE")


@pytest.mark.django_db
def test_agente_recusa_estrategia_invalida_sem_criar(perfil):
    from planner.services import agente

    out = agente._criar_tarefa(perfil, titulo="X", estrategia="ASAP")
    assert out["erro"] == 400
    assert not Tarefa.objects.do_dono(perfil).filter(titulo="X").exists()


@pytest.mark.django_db
def test_criar_tarefa_sem_estrategia_continua_nula(perfil):
    """D2: sem default herdado — o que não for dito continua não dito."""
    from planner.services import agente

    agente._criar_tarefa(perfil, titulo="Sem estratégia")
    assert (
        Tarefa.objects.do_dono(perfil).get(titulo="Sem estratégia").estrategia is None
    )


# --------------------------------------------------------------------------- #
# Integração: o job devolve `leitura` e `perguntas`                            #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_job_devolve_leitura_e_perguntas(perfil):
    from unittest import mock

    from planner import tasks

    t = TarefaFactory(
        dono=perfil,
        titulo="Estudar para a PP1",
        esforco_estimado=120,
        deadline=aware(2026, 6, 19, 13),
        descricao="só consigo de manhã",
    )
    bruto = {
        "diretrizes": {
            "ajustes_por_tarefa": {
                str(t.id): {"janela_inicio": "08:00", "janela_fim": "12:00"}
            }
        },
        "perguntas": [
            {
                "tarefa_id": str(t.id),
                "knob": "estrategia",
                "valor": "TARDE",
                "impacto": 9,
            }
        ],
        "resumo": "ok",
        "trade_offs": [],
        "sugestoes": [],
    }
    with mock.patch("planner.tasks.planejamento_ia.gerar_melhoria", return_value=bruto):
        out = tasks.planejar_ia_task(str(perfil.id), [str(t.id)], SEG.isoformat(), {})

    assert out["leitura"] == [
        {
            "tarefa_id": str(t.id),
            "tarefa": "Estudar para a PP1",
            "entendi": ["só de manhã"],
        }
    ]
    assert len(out["perguntas"]) == 1
    assert out["perguntas"][0]["texto"] == (
        "Quer que «Estudar para a PP1» estuda perto do prazo?"
    )
    # A pergunta NÃO bloqueia: o plano já veio pronto, com o default aplicado.
    assert sum(s["dur_min"] for s in out["plano"]["sessoes"]) == 120


@pytest.mark.django_db
def test_job_sem_ia_traz_as_chaves_vazias(perfil):
    """O front não pode ter dois shapes de resposta para tratar."""
    from unittest import mock

    from planner import tasks

    t = TarefaFactory(
        dono=perfil,
        esforco_estimado=120,
        deadline=aware(2026, 6, 19, 13),
        descricao="só de manhã",
    )
    with mock.patch(
        "planner.tasks.planejamento_ia.gerar_melhoria",
        side_effect=IA.OllamaIndisponivel("down"),
    ):
        out = tasks.planejar_ia_task(str(perfil.id), [str(t.id)], SEG.isoformat(), {})

    assert out["ia_indisponivel"] is True
    assert out["leitura"] == []
    assert out["perguntas"] == []
