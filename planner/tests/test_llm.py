"""Testes da abstração LLMProvider (services/llm.py, Fase 0A.1).

Cobre a costura de transporte: factory por LLM_PROVIDER, o provider Ollama (mockado),
o Mock (no-op) e o wrapping de erro em LLMIndisponivel. Os prompts/schemas/validação
são testados nos services que os usam (test_planejamento_ia, test_cenarios).
"""

import json
from unittest import mock

import pytest

from planner.services import llm


def test_gerar_json_ollama_parse_ok(settings):
    settings.LLM_PROVIDER = "ollama"
    fake = {"message": {"content": json.dumps({"ok": 1})}}
    with mock.patch("planner.services.llm.ollama.Client") as Cli:
        Cli.return_value.chat.return_value = fake
        out = llm.gerar_json(system="s", messages=[], schema={}, familia="planejar_ia")
    assert out == {"ok": 1}


def test_gerar_json_ollama_monta_system_e_messages(settings):
    settings.LLM_PROVIDER = "ollama"
    fake = {"message": {"content": "{}"}}
    with mock.patch("planner.services.llm.ollama.Client") as Cli:
        Cli.return_value.chat.return_value = fake
        llm.gerar_json(
            system="SYS",
            messages=[{"role": "user", "content": "oi"}],
            schema={"a": 1},
            familia="planejar_ia",
        )
        _, kwargs = Cli.return_value.chat.call_args
    assert kwargs["messages"][0] == {"role": "system", "content": "SYS"}
    assert kwargs["messages"][1] == {"role": "user", "content": "oi"}
    assert kwargs["format"] == {"a": 1}
    assert kwargs["options"] == {"temperature": 0}


def test_gerar_json_erro_do_cliente_vira_indisponivel(settings):
    settings.LLM_PROVIDER = "ollama"
    with mock.patch(
        "planner.services.llm.ollama.Client", side_effect=RuntimeError("down")
    ):
        with pytest.raises(llm.LLMIndisponivel):
            llm.gerar_json(system="s", messages=[], schema={}, familia="planejar_ia")


def test_provider_desconhecido_vira_indisponivel(settings):
    settings.LLM_PROVIDER = "gpt-caseiro"
    with pytest.raises(llm.LLMIndisponivel):
        llm.gerar_json(system="s", messages=[], schema={}, familia="planejar_ia")


def test_mock_provider_devolve_dict_vazio(settings):
    settings.LLM_PROVIDER = "mock"
    out = llm.gerar_json(
        system="s",
        messages=[{"role": "user", "content": "x"}],
        schema={},
        familia="planejar_ia",
    )
    assert out == {}


def test_ollama_indisponivel_e_o_mesmo_que_llm_indisponivel():
    # Alias de compat: o except de tasks.py (planejamento_ia.OllamaIndisponivel)
    # precisa pegar o que gerar_json levanta.
    assert llm.OllamaIndisponivel is llm.LLMIndisponivel
    from planner.services import planejamento_ia

    assert planejamento_ia.OllamaIndisponivel is llm.LLMIndisponivel


def test_anthropic_sem_pacote_ou_chave_vira_indisponivel(settings):
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = ""
    settings.LLM_MODEL = "algum-modelo"
    # Sem chave (ou sem o pacote) → LLMIndisponivel, nunca estoura cru.
    with pytest.raises(llm.LLMIndisponivel):
        llm.gerar_json(system="s", messages=[], schema={}, familia="planejar_ia")


def test_anthropic_sem_llm_model_vira_indisponivel(settings):
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = "sk-teste"
    settings.LLM_MODEL = ""
    with pytest.raises(llm.LLMIndisponivel):
        llm.gerar_json(system="s", messages=[], schema={}, familia="planejar_ia")
