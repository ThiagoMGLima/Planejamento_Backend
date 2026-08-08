"""Testes da telemetria de IA (Fase 0A.3).

O que estes testes protegem, em ordem de importância:

1. **Nada de conteúdo no registro** (decisão Q8). É a invariante que não pode
   regredir: o `construir_contexto` carrega a agenda inteira do usuário, e o
   beta roda na máquina de terceiros.
2. **Telemetria nunca derruba a IA** (princípio 6 levado ao subsistema que só
   observa).
3. **A exceção continua propagando** — sem isso o caller para de degradar para
   `ia_indisponivel: true` e o usuário toma erro em vez de plano base.
"""

import json

import pytest

from planner.services import llm


@pytest.fixture
def jsonl(tmp_path, settings):
    """Aponta a telemetria para um arquivo descartável e devolve o leitor."""
    caminho = tmp_path / "tele" / "llm.jsonl"
    settings.TELEMETRIA_JSONL = str(caminho)
    settings.TELEMETRIA_ENABLED = True

    def ler():
        if not caminho.exists():
            return []
        return [json.loads(linha) for linha in caminho.read_text().splitlines()]

    return ler


# Resposta do Ollama com os campos reais medidos em 08/08/2026 (ollama 0.6.2).
RESP_OLLAMA = {
    "model": "qwen2.5:3b-instruct",
    "message": {"role": "assistant", "content": '{"r": "ok"}'},
    "total_duration": 5_460_048_910,
    "load_duration": 4_238_629_226,
    "prompt_eval_count": 35,
    "prompt_eval_duration": 663_082_000,
    "eval_count": 9,
    "eval_duration": 550_996_000,
}


def _chamar(**kw):
    return llm.gerar_json(
        system="s", messages=[{"role": "user", "content": "u"}], schema={}, **kw
    )


# --------------------------------------------------------------------------- #
# Registro do caminho feliz                                                    #
# --------------------------------------------------------------------------- #
def test_registra_tokens_e_duracoes_do_ollama(jsonl, settings, monkeypatch):
    settings.LLM_PROVIDER = "ollama"
    settings.OLLAMA_MODEL = "qwen2.5:3b-instruct"

    class _Cli:
        def __init__(self, **kw):
            pass

        def chat(self, **kw):
            return RESP_OLLAMA

    monkeypatch.setattr(llm.ollama, "Client", _Cli)

    assert _chamar(familia="planejar_ia") == {"r": "ok"}

    (reg,) = jsonl()
    assert reg["familia"] == "planejar_ia"
    assert reg["provider"] == "ollama"
    assert reg["modelo"] == "qwen2.5:3b-instruct"
    assert reg["tokens_entrada"] == 35
    assert reg["tokens_saida"] == 9
    assert reg["ok"] is True
    assert reg["erro"] is None


def test_carga_de_modelo_sai_separada_da_geracao(jsonl, settings, monkeypatch):
    """Q6: `load_duration` não pode ser diluído no total.

    Nesta amostra ele é 78% do tempo. Somado, a matriz da 0A.5 mediria cold
    start em vez de modelo — e o tok/s sairia ~8x menor que o real.
    """
    settings.LLM_PROVIDER = "ollama"

    class _Cli:
        def __init__(self, **kw):
            pass

        def chat(self, **kw):
            return RESP_OLLAMA

    monkeypatch.setattr(llm.ollama, "Client", _Cli)
    _chamar(familia="cenarios")

    (reg,) = jsonl()
    assert reg["carga_s"] == pytest.approx(4.2386, abs=1e-3)
    assert reg["geracao_s"] == pytest.approx(0.551, abs=1e-3)
    # tok/s derivado da geração isolada: 9 / 0.551 ≈ 16.3
    assert reg["tok_s"] == pytest.approx(16.33, abs=0.1)


def test_provider_mock_registra_sem_tokens(jsonl, settings):
    """O mock não tem o que contar, mas a chamada tem de aparecer no histórico."""
    settings.LLM_PROVIDER = "mock"
    assert _chamar(familia="refino") == {}

    (reg,) = jsonl()
    assert reg["provider"] == "mock"
    assert reg["tokens_saida"] is None
    assert reg["ok"] is True
    assert "tok_s" not in reg


def test_dono_entra_no_registro(jsonl, settings, perfil):
    """Custo por usuário ativo/mês é item da Fase 2; sem o dono não se calcula."""
    settings.LLM_PROVIDER = "mock"
    _chamar(familia="planejar_ia", dono_id=perfil.id)

    (reg,) = jsonl()
    assert reg["dono"] == str(perfil.id)


# --------------------------------------------------------------------------- #
# Falhas (Q5)                                                                  #
# --------------------------------------------------------------------------- #
def test_falha_vira_registro_e_a_excecao_propaga(jsonl, settings, monkeypatch):
    settings.LLM_PROVIDER = "ollama"

    class _Cli:
        def __init__(self, **kw):
            pass

        def chat(self, **kw):
            raise TimeoutError("modelo não respondeu")

    monkeypatch.setattr(llm.ollama, "Client", _Cli)

    # Propagar é obrigatório: é assim que o caller degrada para o plano base.
    with pytest.raises(llm.LLMIndisponivel):
        _chamar(familia="planejar_ia")

    (reg,) = jsonl()
    assert reg["ok"] is False
    assert reg["erro"] == "LLMIndisponivel"


def test_registro_de_erro_guarda_a_classe_e_nao_a_mensagem(
    jsonl, settings, monkeypatch
):
    """A mensagem de erro pode ecoar pedaços do payload — seria o Q8 vazando
    pela porta dos fundos."""
    settings.LLM_PROVIDER = "ollama"
    segredo = "Prova de Cálculo III na terça"

    class _Cli:
        def __init__(self, **kw):
            pass

        def chat(self, **kw):
            raise ValueError(f"payload inválido: {segredo}")

    monkeypatch.setattr(llm.ollama, "Client", _Cli)
    with pytest.raises(llm.LLMIndisponivel):
        _chamar(familia="planejar_ia")

    bruto = json.dumps(jsonl())
    assert segredo not in bruto


# --------------------------------------------------------------------------- #
# Invariantes que não podem regredir                                           #
# --------------------------------------------------------------------------- #
def test_nenhum_conteudo_de_prompt_ou_resposta_no_registro(
    jsonl, settings, monkeypatch
):
    """Q8, a invariante central deste módulo."""
    settings.LLM_PROVIDER = "ollama"
    system = "SEGREDO-SYSTEM"
    pedido = "SEGREDO-PEDIDO: reunião com a Ana sobre demissão"
    resposta = "SEGREDO-RESPOSTA"

    class _Cli:
        def __init__(self, **kw):
            pass

        def chat(self, **kw):
            return {**RESP_OLLAMA, "message": {"content": json.dumps({"r": resposta})}}

    monkeypatch.setattr(llm.ollama, "Client", _Cli)
    llm.gerar_json(
        system=system,
        messages=[{"role": "user", "content": pedido}],
        schema={},
        familia="planejar_ia",
    )

    bruto = json.dumps(jsonl())
    for segredo in (system, pedido, resposta, "SEGREDO"):
        assert segredo not in bruto


def test_telemetria_quebrada_nao_derruba_a_chamada(settings, monkeypatch):
    """Princípio 6 aplicado ao observador: se a IA não é caminho crítico,
    observar a IA menos ainda."""
    settings.LLM_PROVIDER = "mock"
    settings.TELEMETRIA_JSONL = "/proc/impossivel/de/escrever/llm.jsonl"

    # Não levanta: a falha de escrita vira warning e o pipeline segue.
    assert _chamar(familia="planejar_ia") == {}


def test_desligada_nao_escreve_nada(jsonl, settings):
    settings.LLM_PROVIDER = "mock"
    settings.TELEMETRIA_ENABLED = False
    _chamar(familia="planejar_ia")
    assert jsonl() == []


def test_familia_e_obrigatoria():
    """Parâmetro do domínio, não ambiente (mesma razão da 0B.10 para o `dono`):
    um registro sem família não serve para nada, e contexto implícito falharia
    em silêncio no worker."""
    with pytest.raises(TypeError):
        llm.gerar_json(system="s", messages=[], schema={})


# --------------------------------------------------------------------------- #
# O agente: N chamadas por turno, não uma                                      #
# --------------------------------------------------------------------------- #
def test_agente_registra_uma_linha_por_chamada(jsonl, settings, monkeypatch, perfil):
    """É o custo multiplicado do tool use que a Fase 2 precisa enxergar."""
    from planner.services import agente

    settings.AGENTE_ENABLED = True
    settings.AGENTE_PROVIDER = "ollama"

    respostas = [
        {
            **RESP_OLLAMA,
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "listar_classes", "arguments": {}}}
                ],
            },
        },
        {**RESP_OLLAMA, "message": {"content": "pronto"}},
    ]

    class _Cli:
        def __init__(self, **kw):
            pass

        def chat(self, **kw):
            return respostas.pop(0)

    import ollama as _ollama

    monkeypatch.setattr(_ollama, "Client", _Cli)

    agente.conversar(perfil, "quais são minhas classes?", {})

    regs = jsonl()
    assert len(regs) == 2, "uma medição por ida ao modelo, não por turno"
    assert {r["familia"] for r in regs} == {"agente"}
