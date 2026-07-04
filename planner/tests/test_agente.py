"""Testes do agente conversacional (Marco C4, o cérebro) — provider mockado.

O cérebro (LLM) e a rede são substituídos: um FakeProvider roteiriza os turnos e
`agente._api` é stubado. Cobre o loop de tool-use (dispatch, acoes, mudou_estado),
o fluxo 202→polling→pronto do endpoint e a degradação sem cérebro. Não sobe
Ollama nem chama a API de verdade.
"""

import json

import pytest
from django.core.cache import cache as django_cache
from rest_framework.test import APIClient

from planner.services import agente


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "agente-tests",
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


class FakeProvider:
    """Cérebro roteirizado: devolve `_Turno`s na ordem dada e registra os
    resultados de ferramenta recebidos."""

    def __init__(self, turnos):
        self._turnos = list(turnos)
        self.resultados = []

    def gerar(self):
        return self._turnos.pop(0)

    def responder_ferramentas(self, resultados):
        self.resultados.append(resultados)
        return self._turnos.pop(0)


def _instalar_provider(monkeypatch, turnos):
    prov = FakeProvider(turnos)
    monkeypatch.setattr(agente, "_criar_provider", lambda hist, msg: prov)
    return prov


def _tc(nome, **args):
    return agente._ToolCall(id="1", nome=nome, args=args)


# --------------------------------------------------------------------------- #
# Loop de tool-use                                                             #
# --------------------------------------------------------------------------- #
def test_conversar_executa_ferramenta_e_responde(monkeypatch):
    monkeypatch.setattr(
        agente, "_api", lambda *a, **k: [{"id": "c1", "nome": "Física"}]
    )
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("listar_classes")]),
            agente._Turno(texto="Você tem a classe Física.", tool_calls=[]),
        ],
    )

    out = agente.conversar("quais classes?", {"hoje": "2026-07-04"})

    assert out["resposta"] == "Você tem a classe Física."
    assert out["ia_indisponivel"] is False
    assert [a["ferramenta"] for a in out["acoes"]] == ["listar_classes"]
    assert out["acoes"][0]["ok"] is True
    # listar_classes é leitura → não recarrega o calendário.
    assert out["mudou_estado"] is False


def test_conversar_criar_tarefa_marca_mudou_estado(monkeypatch):
    monkeypatch.setattr(
        agente, "_api", lambda *a, **k: {"id": "t1", "titulo": "Física 2"}
    )
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="",
                tool_calls=[
                    _tc("criar_tarefa", titulo="Física 2", deadline="2026-07-10")
                ],
            ),
            agente._Turno(texto="Criei a tarefa.", tool_calls=[]),
        ],
    )

    out = agente.conversar("adiciona Física 2", {"hoje": "2026-07-04"})

    assert out["mudou_estado"] is True
    assert out["acoes"][0]["muda_estado"] is True


def test_conversar_replanejar_so_muda_estado_ao_aplicar(monkeypatch):
    monkeypatch.setattr(agente, "_api", lambda *a, **k: {"diff": {}})

    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="", tool_calls=[_tc("replanejar", dias_bloqueados=["2026-07-05"])]
            ),
            agente._Turno(texto="Simulei.", tool_calls=[]),
        ],
    )
    simulou = agente.conversar("como fica sem sábado?", {})
    assert simulou["mudou_estado"] is False  # aplicar ausente → só simulação

    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="",
                tool_calls=[
                    _tc("replanejar", dias_bloqueados=["2026-07-05"], aplicar=True)
                ],
            ),
            agente._Turno(texto="Livrei seu sábado.", tool_calls=[]),
        ],
    )
    aplicou = agente.conversar("livra meu sábado", {})
    assert aplicou["mudou_estado"] is True


def test_conversar_ferramenta_desconhecida_nao_estoura(monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("inexistente")]),
            agente._Turno(texto="Não consegui.", tool_calls=[]),
        ],
    )
    out = agente.conversar("faz algo", {})
    assert out["resposta"] == "Não consegui."
    assert out["acoes"] == []  # nome desconhecido não vira ação registrada


def test_conversar_erro_de_ferramenta_marca_ok_false(monkeypatch):
    monkeypatch.setattr(
        agente, "_api", lambda *a, **k: {"erro": 422, "detalhe": "faltou classe"}
    )
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("criar_tarefa", titulo="X")]),
            agente._Turno(texto="Faltou a classe.", tool_calls=[]),
        ],
    )
    out = agente.conversar("cria X", {})
    assert out["acoes"][0]["ok"] is False
    assert out["mudou_estado"] is False  # erro não recarrega o calendário


def test_criar_tarefa_com_classe_invalida_devolve_erro_acionavel(monkeypatch):
    """E2E com o 7B: o modelo chuta classe_id e desiste do erro cru. O erro
    precisa voltar com as classes reais + dica para o modelo se corrigir."""

    def fake_api(metodo, caminho, corpo=None, params=None):
        if caminho == "/tarefas/":
            return {"erro": 400, "detalhe": {"classe_id": ['Pk inválido "1".']}}
        if caminho == "/classes/":
            return {"results": [{"id": "uuid-estudar", "nome": "Estudar"}]}
        raise AssertionError(f"caminho inesperado: {caminho}")

    monkeypatch.setattr(agente, "_api", fake_api)
    out = agente._criar_tarefa("X", classe_id="1")
    assert out["erro"] == 400
    assert out["classes_disponiveis"] == [{"id": "uuid-estudar", "nome": "Estudar"}]
    assert "criar_tarefa" in out["dica"]


def test_criar_tarefa_erro_sem_classe_id_nao_busca_classes(monkeypatch):
    """Erro que não é de classe (ex.: deadline inválida) passa reto, sem a
    chamada extra a /classes/."""
    chamadas = []

    def fake_api(metodo, caminho, corpo=None, params=None):
        chamadas.append(caminho)
        return {"erro": 400, "detalhe": {"deadline": ["Inválida."]}}

    monkeypatch.setattr(agente, "_api", fake_api)
    out = agente._criar_tarefa("X", deadline="ontem")
    assert "classes_disponiveis" not in out
    assert chamadas == ["/tarefas/"]


def test_conversar_injeta_classes_nos_fatos(monkeypatch):
    """Grounding: as classes reais entram nos FATOS do pedido — o 7B copia o
    id em vez de precisar do salto listar_classes → criar_tarefa (que ele
    não faz: chuta ids, vimos no E2E)."""
    monkeypatch.setattr(
        agente,
        "_api",
        lambda *a, **k: {"results": [{"id": "uuid-estudar", "nome": "Estudar"}]},
    )
    pedidos = []

    def fake_criar_provider(historico, pedido):
        pedidos.append(pedido)
        return FakeProvider([agente._Turno(texto="ok", tool_calls=[])])

    monkeypatch.setattr(agente, "_criar_provider", fake_criar_provider)
    agente.conversar("oi", {"hoje": "2026-07-04"})
    assert "uuid-estudar" in pedidos[0]  # classes viraram FATOS
    assert "hoje" in pedidos[0]  # contexto original preservado


def test_conversar_classes_fora_do_ar_segue_sem_elas(monkeypatch):
    monkeypatch.setattr(agente, "_api", lambda *a, **k: {"erro": "rede"})
    _instalar_provider(monkeypatch, [agente._Turno(texto="ok", tool_calls=[])])
    out = agente.conversar("oi", {})  # não levanta
    assert out["resposta"] == "ok"


def test_conversar_injeta_calendario_nos_fatos(monkeypatch):
    """Data é aritmética: a tabela dos próximos dias ancora "segunda que vem"
    (no E2E o 7B apontou uma sexta)."""
    monkeypatch.setattr(agente, "_api", lambda *a, **k: {"erro": "rede"})
    pedidos = []

    def fake_criar_provider(historico, pedido):
        pedidos.append(pedido)
        return FakeProvider([agente._Turno(texto="ok", tool_calls=[])])

    monkeypatch.setattr(agente, "_criar_provider", fake_criar_provider)
    agente.conversar("o que tenho sexta?", {})
    fatos = json.loads(pedidos[0].split("\n\nPedido")[0].split(":\n", 1)[1])
    assert len(fatos["calendario"]) == 8
    from django.utils import timezone as tz

    hoje = tz.localdate()
    assert fatos["calendario"][0] == {
        "data": hoje.isoformat(),
        "dia_da_semana": agente.DIAS_PT[hoje.weekday()],
    }


def test_consultar_agenda_localiza_horarios(monkeypatch):
    """A API fala UTC; o modelo lia '22:00Z' como 22h. A ferramenta entrega
    horário local — o modelo só copia."""

    def fake_api(metodo, caminho, corpo=None, params=None):
        return [
            {
                "id": "e1",
                "inicio": "2026-07-06T22:00:00Z",
                "fim": "2026-07-06T23:30:00Z",
            },
            {"id": "e2", "inicio": None, "fim": "nada-a-ver"},  # não explode
        ]

    monkeypatch.setattr(agente, "_api", fake_api)
    out = agente._consultar_agenda(
        "2026-07-06T00:00:00-03:00", "2026-07-07T00:00:00-03:00"
    )
    assert out[0]["inicio"] == "2026-07-06T19:00:00-03:00"  # America/Sao_Paulo
    assert out[0]["fim"] == "2026-07-06T20:30:00-03:00"
    assert out[1]["fim"] == "nada-a-ver"  # valor estranho passa reto


def test_conversar_desligado_levanta(monkeypatch, settings):
    settings.AGENTE_ENABLED = False
    with pytest.raises(agente.AgenteIndisponivel):
        agente.conversar("oi", {})


# --------------------------------------------------------------------------- #
# Endpoint 202 → polling → pronto                                             #
# --------------------------------------------------------------------------- #
def test_endpoint_chat_fluxo_completo(api, eager, monkeypatch):
    monkeypatch.setattr(
        agente, "_api", lambda *a, **k: [{"id": "c1", "nome": "Física"}]
    )
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("listar_classes")]),
            agente._Turno(texto="Tem Física.", tool_calls=[]),
        ],
    )

    r = api.post(
        "/api/v1/planejamento/agente/chat",
        {
            "conversa_id": "conv-1",
            "mensagem": "quais classes?",
            "contexto": {"hoje": "2026-07-04"},
        },
        format="json",
    )
    assert r.status_code == 202
    job_id = r.data["job_id"]

    s = api.get(f"/api/v1/planejamento/agente/chat/{job_id}")
    assert s.status_code == 200
    assert s.data["status"] == "pronto"
    assert s.data["resultado"]["resposta"] == "Tem Física."


def test_endpoint_degrada_sem_cerebro(api, eager, monkeypatch):
    def _cai(hist, msg):
        raise agente.AgenteIndisponivel("provider fora")

    monkeypatch.setattr(agente, "_criar_provider", _cai)

    r = api.post(
        "/api/v1/planejamento/agente/chat",
        {"conversa_id": "conv-2", "mensagem": "oi"},
        format="json",
    )
    assert r.status_code == 202
    s = api.get(f"/api/v1/planejamento/agente/chat/{r.data['job_id']}")
    assert s.data["status"] == "pronto"
    assert s.data["resultado"]["ia_indisponivel"] is True


def test_endpoint_memoria_da_conversa_reenvia_historico(api, eager, monkeypatch):
    capturado = {}

    def _fake_criar(historico, mensagem):
        capturado["historico"] = list(historico)
        return FakeProvider([agente._Turno(texto="ok", tool_calls=[])])

    monkeypatch.setattr(agente, "_criar_provider", _fake_criar)

    corpo = {"conversa_id": "conv-3", "mensagem": "primeira"}
    api.post("/api/v1/planejamento/agente/chat", corpo, format="json")
    assert capturado["historico"] == []  # 1º turno: sem memória

    api.post(
        "/api/v1/planejamento/agente/chat",
        {"conversa_id": "conv-3", "mensagem": "segunda"},
        format="json",
    )
    # 2º turno: o texto do 1º turno (user+assistant) foi reenviado.
    assert {"role": "user", "content": "primeira"} in capturado["historico"]
    assert {"role": "assistant", "content": "ok"} in capturado["historico"]
