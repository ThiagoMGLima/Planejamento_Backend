"""Testes de contrato do servidor MCP (Marco C4) — API HTTP mockada com respx.

O servidor é uma camada fina: cada tool precisa bater no endpoint certo com o
corpo certo e devolver o corpo da API sem inventar nada. Erros HTTP viram dict
com `erro` (o agente lê o motivo), nunca exceção.
"""

import asyncio
import json

import httpx
import pytest
import respx

from mcp_server import server

BASE = "http://web:8000/api/v1"


@pytest.fixture(autouse=True)
def _api_local(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", BASE)
    monkeypatch.setenv("MCP_POLL_INTERVALO_S", "0")
    monkeypatch.setenv("MCP_POLL_TIMEOUT_S", "1")


def _run(coro):
    return asyncio.run(coro)


@respx.mock
def test_criar_tarefa_espelha_o_contrato():
    rota = respx.post(f"{BASE}/tarefas/").mock(
        return_value=httpx.Response(201, json={"id": "t1", "titulo": "Física 2"})
    )
    out = _run(
        server.criar_tarefa(
            "Física 2",
            classe_id="c1",
            deadline="2026-07-10T18:00:00-03:00",
            esforco_min=120,
        )
    )
    assert out["id"] == "t1"
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {
        "titulo": "Física 2",
        "descricao": "",
        "classe_id": "c1",
        "deadline": "2026-07-10T18:00:00-03:00",
        "esforco_estimado": 120,
    }


@respx.mock
def test_listar_classes_desembrulha_paginacao():
    respx.get(f"{BASE}/classes/").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "c1", "nome": "Estudo"}], "next": None}
        )
    )
    assert _run(server.listar_classes()) == [{"id": "c1", "nome": "Estudo"}]


@respx.mock
def test_listar_pendentes():
    respx.get(f"{BASE}/pendentes").mock(return_value=httpx.Response(200, json=[]))
    assert _run(server.listar_pendentes()) == []


@respx.mock
def test_listar_tarefas_filtra_e_segue_o_cursor():
    # Rota do cursor primeiro: o respx casa params por SUBCONJUNTO, e a página 2
    # também carrega status=INBOX — definida depois, nunca seria alcançada.
    respx.get(f"{BASE}/tarefas/", params={"cursor": "abc"}).mock(
        return_value=httpx.Response(200, json={"results": [{"id": "t2"}], "next": None})
    )
    respx.get(f"{BASE}/tarefas/", params={"status": "INBOX"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "t1"}],
                "next": f"{BASE}/tarefas/?cursor=abc&status=INBOX",
            },
        )
    )
    assert _run(server.listar_tarefas(status="INBOX")) == [{"id": "t1"}, {"id": "t2"}]


@respx.mock
def test_consultar_agenda_usa_janela_obrigatoria():
    rota = respx.get(f"{BASE}/eventos/").mock(
        return_value=httpx.Response(200, json=[{"id": "e1"}])
    )
    out = _run(
        server.consultar_agenda(
            "2026-07-06T00:00:00-03:00", "2026-07-12T23:59:00-03:00"
        )
    )
    assert out == [{"id": "e1"}]
    params = rota.calls.last.request.url.params
    assert params["inicio"] == "2026-07-06T00:00:00-03:00"
    assert params["fim"] == "2026-07-12T23:59:00-03:00"


@respx.mock
def test_concluir_ocorrencia_envia_real_min():
    rota = respx.post(f"{BASE}/eventos/e1/concluir/").mock(
        return_value=httpx.Response(
            200,
            json={"evento": "e1", "data": "2026-07-03", "status_override": "CONCLUIDO"},
        )
    )
    out = _run(
        server.concluir("e1", escopo="ocorrencia", data="2026-07-03", real_min=90)
    )
    assert out["status_override"] == "CONCLUIDO"
    req = rota.calls.last.request
    assert req.url.params["escopo"] == "ocorrencia"
    assert req.url.params["data"] == "2026-07-03"
    assert json.loads(req.content) == {"real_min": 90}


@respx.mock
def test_simular_plano_nao_persiste_e_repassa_o_plano():
    rota = respx.post(f"{BASE}/planejamento/calcular").mock(
        return_value=httpx.Response(200, json={"sessoes": [], "nao_alocado": []})
    )
    out = _run(server.simular_plano(["t1"], horizonte="SEMANA"))
    assert out == {"sessoes": [], "nao_alocado": []}
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {"tarefa_ids": ["t1"], "horizonte": "SEMANA"}


@respx.mock
def test_gerar_cenarios_encapsula_o_polling():
    respx.post(f"{BASE}/planejamento/cenarios").mock(
        return_value=httpx.Response(202, json={"job_id": "j1", "status": "processando"})
    )
    respx.get(f"{BASE}/planejamento/cenarios/j1").mock(
        side_effect=[
            httpx.Response(200, json={"status": "processando"}),
            httpx.Response(
                200,
                json={
                    "status": "pronto",
                    "resultado": {"cenarios": [], "pesos_usados": {}},
                },
            ),
        ]
    )
    out = _run(server.gerar_cenarios(["t1"]))
    assert out["job_id"] == "j1"  # necessário para escolher_cenario
    assert out["cenarios"] == []


@respx.mock
def test_gerar_cenarios_cache_hit_devolve_direto():
    respx.post(f"{BASE}/planejamento/cenarios").mock(
        return_value=httpx.Response(
            200, json={"status": "pronto", "resultado": {"cenarios": ["x"]}}
        )
    )
    assert _run(server.gerar_cenarios(["t1"]))["cenarios"] == ["x"]


@respx.mock
def test_gerar_cenarios_timeout_vira_erro():
    respx.post(f"{BASE}/planejamento/cenarios").mock(
        return_value=httpx.Response(202, json={"job_id": "j1", "status": "processando"})
    )
    respx.get(f"{BASE}/planejamento/cenarios/j1").mock(
        return_value=httpx.Response(200, json={"status": "processando"})
    )
    out = _run(server.gerar_cenarios(["t1"]))
    assert out["erro"] == 504
    assert out["job_id"] == "j1"


@respx.mock
def test_gerar_cenarios_job_sumido_no_polling_nao_espera_o_timeout():
    respx.post(f"{BASE}/planejamento/cenarios").mock(
        return_value=httpx.Response(202, json={"job_id": "j1", "status": "processando"})
    )
    respx.get(f"{BASE}/planejamento/cenarios/j1").mock(
        return_value=httpx.Response(404, json={"detail": "Job desconhecido."})
    )
    out = _run(server.gerar_cenarios(["t1"]))
    assert out["erro"] == 404  # devolve o motivo real, não um 504 tardio
    assert out["job_id"] == "j1"


@respx.mock
def test_refinar_cenario_encapsula_o_polling_e_preserva_o_job_do_lote():
    rota = respx.post(f"{BASE}/planejamento/cenarios/refinar").mock(
        return_value=httpx.Response(202, json={"job_id": "r1", "status": "processando"})
    )
    respx.get(f"{BASE}/planejamento/cenarios/refinar/r1").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "pronto",
                "resultado": {"resposta": "feito", "cenario": {"id": "b-sem-academia"}},
            },
        )
    )
    out = _run(server.refinar_cenario("j1", "sem academia essa semana", cenario_id="b"))
    assert out["job_id"] == "j1"  # lote continua endereçado pelo job original
    assert out["cenario"]["id"] == "b-sem-academia"
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {
        "job_id": "j1",
        "mensagem": "sem academia essa semana",
        "cenario_id": "b",
    }


@respx.mock
def test_refinar_cenario_lote_expirado_vira_erro():
    respx.post(f"{BASE}/planejamento/cenarios/refinar").mock(
        return_value=httpx.Response(404, json={"job_id": ["Job desconhecido."]})
    )
    out = _run(server.refinar_cenario("morto", "sem academia"))
    assert out["erro"] == 404


@respx.mock
def test_escolher_cenario():
    rota = respx.post(f"{BASE}/planejamento/cenarios/escolher").mock(
        return_value=httpx.Response(200, json={"aplicado": True, "eventos_criados": 3})
    )
    out = _run(server.escolher_cenario("j1", "sabado-livre", aplicar=True))
    assert out["aplicado"] is True
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {"job_id": "j1", "cenario_id": "sabado-livre", "aplicar": True}


@respx.mock
def test_replanejar_separa_simular_de_aplicar():
    simular = respx.post(f"{BASE}/planejamento/replanejar").mock(
        return_value=httpx.Response(200, json={"plano": {}, "diff": {}})
    )
    aplicar = respx.post(f"{BASE}/planejamento/replanejar/aplicar").mock(
        return_value=httpx.Response(200, json={"diff": {}, "eventos_criados": 2})
    )
    _run(server.replanejar(dias_bloqueados=["2026-07-03"]))
    assert simular.called and not aplicar.called
    _run(server.replanejar(dias_bloqueados=["2026-07-03"], aplicar=True))
    assert aplicar.called
    corpo = json.loads(aplicar.calls.last.request.content)
    assert corpo == {"dias_bloqueados": ["2026-07-03"]}


@respx.mock
def test_remarcar_usa_query_params():
    rota = respx.post(f"{BASE}/eventos/e1/remarcar/").mock(
        return_value=httpx.Response(200, json={"tarefa_reaberta": {"id": "t1"}})
    )
    out = _run(server.remarcar("e1", escopo="ocorrencia", data="2026-07-03"))
    assert out["tarefa_reaberta"]["id"] == "t1"
    assert rota.calls.last.request.url.params["escopo"] == "ocorrencia"
    assert rota.calls.last.request.url.params["data"] == "2026-07-03"


@respx.mock
def test_erro_http_vira_dict_e_nao_excecao():
    respx.post(f"{BASE}/planejamento/calcular").mock(
        return_value=httpx.Response(
            422, json={"tarefas_invalidas": [{"tarefa_id": "x"}]}
        )
    )
    out = _run(server.simular_plano(["x"]))
    assert out["erro"] == 422
    assert out["detalhe"]["tarefas_invalidas"]


def test_tools_registradas_no_servidor():
    esperadas = {
        "criar_tarefa",
        "listar_classes",
        "listar_tarefas",
        "listar_pendentes",
        "consultar_agenda",
        "concluir",
        "simular_plano",
        "gerar_cenarios",
        "refinar_cenario",
        "escolher_cenario",
        "replanejar",
        "remarcar",
    }
    registradas = {t.name for t in _run(server.mcp.list_tools())}
    assert esperadas <= registradas
