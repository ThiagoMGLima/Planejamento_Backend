"""Ferramenta `atualizar_tarefa` (Fase 1.1, PR C2).

Nasceu de um incidente no dogfooding de 15/08/2026: pedido de "faça essas duas
terminarem na véspera da prova" fez o modelo chamar `criar_tarefa` e **duplicar**
as tarefas no banco real. Não era limitação do modelo — `criar_tarefa` era a
única ferramenta de escrita que existia. Editar era impossível.

O que estes testes travam:

1. **Editar edita** — e não cria nada novo (a regressão do incidente).
2. **Nulo é omissão, não apagamento.** Só `limpar` apaga, e explicitamente.
3. **Título e descrição não são atualizáveis** — a descrição virou insumo de
   planejamento (PR C), e a IA reescrevê-la seria editar a própria entrada.
4. **A validação é a MESMA da API**, porque agora tem fonte única no service.
"""

from datetime import date, time, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from planner.models import Tarefa
from planner.services import agente, tarefas

from .factories import TarefaFactory


@pytest.fixture
def api():
    return APIClient()


def _tarefa(perfil, **kw):
    kw.setdefault("esforco_estimado", 120)
    kw.setdefault("deadline", timezone.now() + timedelta(days=20))
    return TarefaFactory(dono=perfil, **kw)


# --------------------------------------------------------------------------- #
# O caso que originou a ferramenta                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_edita_a_tarefa_existente_sem_criar_outra(perfil):
    """A regressão do incidente: editar não pode virar criar."""
    t = _tarefa(perfil, titulo="Estudar para a PP1")
    antes = Tarefa.objects.do_dono(perfil).count()

    out = agente._atualizar_tarefa(perfil, str(t.id), nao_depois_de="2026-10-19")

    assert "erro" not in out, out
    assert Tarefa.objects.do_dono(perfil).count() == antes
    t.refresh_from_db()
    assert t.nao_depois_de == date(2026, 10, 19)
    assert out["alterado"] == ["nao_depois_de"]


@pytest.mark.django_db
def test_resumo_em_portugues_sem_jargao(perfil):
    from .test_vocabulario import assert_sem_jargao

    t = _tarefa(perfil)
    out = agente._atualizar_tarefa(
        perfil, str(t.id), estrategia="TARDE", janela_inicio="08:00", janela_fim="12:00"
    )
    assert out["resumo"] == ["estuda perto do prazo", "só de manhã"]
    for frase in out["resumo"]:
        assert_sem_jargao(frase)


# --------------------------------------------------------------------------- #
# Nulo é omissão; só `limpar` apaga                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_campo_omitido_fica_como_esta(perfil):
    t = _tarefa(
        perfil, estrategia=Tarefa.Estrategia.TARDE, nao_antes_de=date(2026, 9, 1)
    )

    agente._atualizar_tarefa(perfil, str(t.id), esforco_min=300)

    t.refresh_from_db()
    assert t.esforco_estimado == 300
    assert t.estrategia == "TARDE"  # não foi zerado por vir None
    assert t.nao_antes_de == date(2026, 9, 1)


@pytest.mark.django_db
def test_limpar_apaga_a_restricao(perfil):
    t = _tarefa(perfil, nao_antes_de=date(2026, 9, 1), estrategia="TARDE")

    out = agente._atualizar_tarefa(perfil, str(t.id), limpar=["nao_antes_de"])

    assert "erro" not in out, out
    t.refresh_from_db()
    assert t.nao_antes_de is None
    assert t.estrategia == "TARDE"  # o resto fica


@pytest.mark.django_db
def test_limpar_campo_desconhecido_da_400(perfil):
    t = _tarefa(perfil)
    out = agente._atualizar_tarefa(perfil, str(t.id), limpar=["titulo"])
    assert out["erro"] == 400


@pytest.mark.django_db
def test_chamada_sem_nenhum_campo_da_400(perfil):
    t = _tarefa(perfil)
    assert agente._atualizar_tarefa(perfil, str(t.id))["erro"] == 400


# --------------------------------------------------------------------------- #
# Escopo e entrada inválida                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_tarefa_de_outro_perfil_e_inexistente(perfil, outro_perfil):
    alheia = _tarefa(outro_perfil)
    out = agente._atualizar_tarefa(perfil, str(alheia.id), estrategia="TARDE")

    assert out["erro"] == 404
    alheia.refresh_from_db()
    assert alheia.estrategia is None


@pytest.mark.django_db
def test_id_que_nem_e_uuid_nao_explode(perfil):
    """O 7B às vezes manda o TÍTULO no lugar do id."""
    out = agente._atualizar_tarefa(perfil, "Estudar para a PP1", estrategia="TARDE")
    assert out["erro"] == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "campos",
    [
        {"estrategia": "ASAP"},
        {"janela_inicio": "08:00"},  # sem par
        {"janela_inicio": "18:00", "janela_fim": "09:00"},  # invertida
        {"dias_permitidos": []},
        {"dias_permitidos": [0, 9]},
        {"nao_antes_de": "2026-10-20", "nao_depois_de": "2026-10-10"},
        {"esforco_min": 0},
        {"deadline": "ontem"},
        {"nao_antes_de": "20 de outubro"},
    ],
)
def test_entrada_invalida_da_400_e_nao_grava(perfil, campos):
    t = _tarefa(perfil)
    antes = (t.estrategia, t.nao_antes_de, t.esforco_estimado)

    out = agente._atualizar_tarefa(perfil, str(t.id), **campos)

    assert out["erro"] == 400, out
    t.refresh_from_db()
    assert (t.estrategia, t.nao_antes_de, t.esforco_estimado) == antes


@pytest.mark.django_db
def test_validacao_considera_o_que_ja_esta_na_tarefa(perfil):
    """Combinação inválida formada com o que já existia tem de ser barrada."""
    t = _tarefa(perfil, nao_depois_de=date(2026, 10, 10))

    out = agente._atualizar_tarefa(perfil, str(t.id), nao_antes_de="2026-10-20")

    assert out["erro"] == 400
    t.refresh_from_db()
    assert t.nao_antes_de is None


# --------------------------------------------------------------------------- #
# Fonte única de validação: API e ferramenta concordam                         #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_api_e_ferramenta_recusam_a_mesma_coisa(api, perfil):
    t = _tarefa(perfil)
    payload = {"janela_inicio": "18:00", "janela_fim": "09:00"}

    via_api = api.patch(f"/api/v1/tarefas/{t.id}/", payload, format="json")
    via_ferramenta = agente._atualizar_tarefa(perfil, str(t.id), **payload)

    assert via_api.status_code == 400
    assert via_ferramenta["erro"] == 400
    assert "janela_fim" in via_api.json()
    assert "janela_fim" in via_ferramenta["detalhe"]


@pytest.mark.django_db
def test_service_recusa_campo_fora_da_lista(perfil):
    """`titulo`/`descricao` não são atualizáveis por aqui, por desenho."""
    t = _tarefa(perfil)
    with pytest.raises(tarefas.ParametrosInvalidos):
        tarefas.atualizar(perfil, str(t.id), titulo="outro")
    with pytest.raises(tarefas.ParametrosInvalidos):
        tarefas.atualizar(perfil, str(t.id), descricao="outra")


# --------------------------------------------------------------------------- #
# Registro e loop de tool-use                                                  #
# --------------------------------------------------------------------------- #
def test_ferramenta_registrada_e_muda_estado():
    ferr = agente.FERRAMENTAS_POR_NOME["atualizar_tarefa"]
    assert ferr["muda_estado"] is True
    assert ferr["parametros"]["required"] == ["tarefa_id"]
    assert "nao_depois_de" in ferr["parametros"]["properties"]


@pytest.mark.django_db
def test_conversar_atualizando_marca_mudou_estado(monkeypatch, perfil):
    from .test_agente import _instalar_provider, _tc

    t = _tarefa(perfil)
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="",
                tool_calls=[
                    _tc(
                        "atualizar_tarefa",
                        tarefa_id=str(t.id),
                        nao_depois_de="2026-10-19",
                    )
                ],
            ),
            agente._Turno(texto="Ajustei.", tool_calls=[]),
        ],
    )

    out = agente.conversar(perfil, "faça terminar na véspera", {})

    assert out["mudou_estado"] is True
    assert out["acoes"][0]["ok"] is True
    t.refresh_from_db()
    assert t.nao_depois_de == date(2026, 10, 19)


@pytest.mark.django_db
def test_efeito_no_solver_e_real(perfil):
    """A alteração não é cosmética: muda o plano."""
    from planner.services import planejamento as P

    prazo = timezone.localtime(timezone.now()) + timedelta(days=20)
    prazo = prazo.replace(hour=18, minute=0, second=0, microsecond=0)
    t = _tarefa(perfil, deadline=prazo, esforco_estimado=60, estrategia="TARDE")

    agente._atualizar_tarefa(
        perfil, str(t.id), nao_depois_de=(prazo - timedelta(days=2)).date().isoformat()
    )
    t.refresh_from_db()
    res = P.montar_plano(perfil, [t], timezone.now(), {})

    assert res.sessoes
    for s in res.sessoes:
        assert timezone.localtime(s.inicio).date() <= (prazo - timedelta(days=2)).date()


@pytest.mark.django_db
def test_hora_e_gravada_como_time(perfil):
    t = _tarefa(perfil)
    agente._atualizar_tarefa(
        perfil, str(t.id), janela_inicio="08:30", janela_fim="12:00"
    )
    t.refresh_from_db()
    assert t.janela_inicio == time(8, 30)
    assert t.janela_fim == time(12, 0)
