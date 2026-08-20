"""Ajudar o modelo a escolher a ferramenta certa (Fase 1.1, PR C3).

Três medições de 15/08/2026 mostraram o mesmo padrão: o 7b **executa** bem
instrução explícita e única, e erra quando precisa **descobrir** o id de algo ou
**escolher** entre ferramentas parecidas. Pedido para ALTERAR uma tarefa virava
`criar_tarefa` — duas vezes, e na segunda mesmo com `atualizar_tarefa`
disponível e a descrição dizendo "nunca use criar_tarefa para isso".

O diagnóstico: ele não escolhia criar; caía na **única porta aberta**. Sem os
ids à mão, `criar_tarefa` era a única escrita que não exigia um.

Duas correções, ambas padrões que o projeto já usava:

1. **Grounding** — as tarefas entram nos FATOS, como classes e datas já entram.
   O id vira cópia, não descoberta.
2. **Recusa acionável** — criar com título parecido com um existente devolve os
   candidatos e a dica, como o erro de `classe_id` já fazia. Escrita errada
   silenciosa vira ciclo corretivo.

Nenhuma das duas é muleta para o 7b: as duas melhoram qualquer modelo.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from planner.models import Tarefa
from planner.services import agente, tarefas

from .factories import TarefaFactory


def _tarefa(perfil, titulo, dias=10, **kw):
    return TarefaFactory(
        dono=perfil,
        titulo=titulo,
        deadline=timezone.now() + timedelta(days=dias),
        esforco_estimado=120,
        **kw,
    )


# --------------------------------------------------------------------------- #
# 1. Grounding: as tarefas entram nos FATOS                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_fatos_levam_id_e_titulo_das_tarefas(perfil):
    t = _tarefa(perfil, "Estudar para a PP1 — diodos")

    fatos = agente._tarefas_para_fatos(perfil)

    assert len(fatos) == 1
    assert set(fatos[0]) == {"id", "titulo", "prazo", "status"}
    assert fatos[0]["id"] == str(t.id)
    assert fatos[0]["titulo"] == "Estudar para a PP1 — diodos"


@pytest.mark.django_db
def test_conversar_injeta_tarefas_no_pedido(monkeypatch, perfil):
    """O id precisa chegar ao modelo — senão o grounding não existe."""
    t = _tarefa(perfil, "Estudar para a Redes Avaliação 1")
    capturado = {}

    def fake(historico, mensagem, dono_id=None):
        capturado["mensagem"] = mensagem

        class P:
            def gerar(self):
                return agente._Turno(texto="ok", tool_calls=[])

        return P()

    monkeypatch.setattr(agente, "_criar_provider", fake)
    agente.conversar(perfil, "mude a tarefa de Redes", {})

    assert str(t.id) in capturado["mensagem"]
    assert "Estudar para a Redes Avaliação 1" in capturado["mensagem"]


@pytest.mark.django_db
def test_tarefa_vencida_nao_entra_nos_fatos(perfil):
    _tarefa(perfil, "Prova que já passou", dias=-5)
    assert agente._tarefas_para_fatos(perfil) == []


@pytest.mark.django_db
def test_tarefa_sem_prazo_nao_entra_nos_fatos(perfil):
    TarefaFactory(dono=perfil, titulo="Sem prazo nenhum", deadline=None)
    assert agente._tarefas_para_fatos(perfil) == []


@pytest.mark.django_db
def test_fatos_ordenam_por_prazo_e_respeitam_o_teto(perfil):
    """Payload grande foi o que fez o 7b alucinar antes — o teto é proposital."""
    for i in range(agente.MAX_TAREFAS_NOS_FATOS + 5):
        _tarefa(perfil, f"Tarefa número {i}", dias=i + 1)

    fatos = agente._tarefas_para_fatos(perfil)

    assert len(fatos) == agente.MAX_TAREFAS_NOS_FATOS
    prazos = [f["prazo"] for f in fatos]
    assert prazos == sorted(prazos)  # as mais próximas primeiro


@pytest.mark.django_db
def test_contexto_explicito_vence_o_grounding(perfil, outro_perfil):
    """Quem já mandou `tarefas` no contexto manda — mesma regra de `classes`."""
    _tarefa(perfil, "Uma tarefa qualquer minha")
    capturado = {}

    def fake(historico, mensagem, dono_id=None):
        capturado["mensagem"] = mensagem

        class P:
            def gerar(self):
                return agente._Turno(texto="ok", tool_calls=[])

        return P()

    monkeypatch_alvo = agente
    original = monkeypatch_alvo._criar_provider
    monkeypatch_alvo._criar_provider = fake
    try:
        agente.conversar(perfil, "oi", {"tarefas": []})
    finally:
        monkeypatch_alvo._criar_provider = original

    assert '"tarefas": []' in capturado["mensagem"]


# --------------------------------------------------------------------------- #
# 2. Recusa acionável ao criar com título parecido                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_criar_com_titulo_prefixo_de_existente_e_recusado(perfil):
    """O caso literal do incidente: o modelo mandou um PREFIXO do título real."""
    real = _tarefa(perfil, "Estudar para a PP1 — diodos e transistor bipolar")
    antes = Tarefa.objects.do_dono(perfil).count()

    out = agente._criar_tarefa(perfil, titulo="Estudar para a PP1")

    assert out["erro"] == 409
    assert Tarefa.objects.do_dono(perfil).count() == antes  # nada criado
    assert out["tarefas_parecidas"][0]["id"] == str(real.id)
    assert "atualizar_tarefa" in out["dica"]


@pytest.mark.django_db
def test_recusa_traz_o_id_para_o_modelo_corrigir(perfil):
    """A dica só serve se vier com o id — senão o modelo fica no mesmo lugar."""
    real = _tarefa(perfil, "Estudar para a Redes Avaliação 1 — camada de rede")

    out = agente._criar_tarefa(perfil, titulo="Estudar para a Redes Avaliação 1")
    parecida = out["tarefas_parecidas"][0]

    # O turno seguinte funciona: o id devolvido serve para atualizar_tarefa.
    seguinte = agente._atualizar_tarefa(perfil, parecida["id"], estrategia="TARDE")
    assert "erro" not in seguinte, seguinte
    real.refresh_from_db()
    assert real.estrategia == "TARDE"


@pytest.mark.django_db
def test_permitir_parecida_e_a_saida_explicita(perfil):
    _tarefa(perfil, "Estudar para a PP1 — diodos e transistor bipolar")

    out = agente._criar_tarefa(
        perfil, titulo="Estudar para a PP1", permitir_parecida=True
    )

    assert "erro" not in out, out
    assert Tarefa.objects.do_dono(perfil).filter(titulo="Estudar para a PP1").exists()


@pytest.mark.django_db
def test_titulo_diferente_cria_normalmente(perfil):
    _tarefa(perfil, "Estudar para a PP1 — diodos e transistor bipolar")

    out = agente._criar_tarefa(perfil, titulo="Comprar cabo HDMI para o projeto")

    assert "erro" not in out, out


@pytest.mark.django_db
def test_titulo_curto_nao_dispara_falso_positivo(perfil):
    """ "Prova" está dentro de meia agenda — abaixo do mínimo, não compara."""
    _tarefa(perfil, "Estudar para a Prova de Cálculo")

    out = agente._criar_tarefa(perfil, titulo="Prova")

    assert "erro" not in out, out


@pytest.mark.django_db
def test_comparacao_ignora_acento_caixa_e_espaco(perfil):
    _tarefa(perfil, "Estudar para a Avaliação de Redes")

    out = agente._criar_tarefa(perfil, titulo="estudar   para a  AVALIACAO de redes")

    assert out["erro"] == 409


@pytest.mark.django_db
def test_parecidas_e_escopado_por_dono(perfil, outro_perfil):
    """Tarefa de outro perfil não pode nem ser detectada — vazaria título."""
    TarefaFactory(dono=outro_perfil, titulo="Estudar para a PP1 do outro perfil")

    assert tarefas.parecidas(perfil, "Estudar para a PP1 do outro perfil") == []


@pytest.mark.django_db
def test_dica_aponta_as_duas_saidas(perfil):
    """A dica é instrução para o MODELO (não texto do usuário): tem de dizer as
    duas saídas — corrigir para atualizar_tarefa, ou insistir de propósito."""
    _tarefa(perfil, "Estudar para a PP1 — diodos e transistor bipolar")

    dica = agente._criar_tarefa(perfil, titulo="Estudar para a PP1")["dica"]

    assert "atualizar_tarefa" in dica
    assert "permitir_parecida" in dica


@pytest.mark.django_db
def test_schema_expoe_a_saida_explicita():
    props = agente.FERRAMENTAS_POR_NOME["criar_tarefa"]["parametros"]["properties"]
    assert "permitir_parecida" in props
