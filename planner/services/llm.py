"""Abstração `LLMProvider` para a forma *chamada única + JSON schema* (Fase 0A.1).

Este módulo é o irmão de `agente.py` para a **outra** forma de falar com o LLM. São
duas de propósito diferente, e por isso duas abstrações e dois envs:

- `agente.py` — **multi-turno com tool use**, stateful (`gerar`/`responder_ferramentas`),
  escolhido por `AGENTE_PROVIDER`. O agente quer um modelo forte (remoto).
- este módulo — **1 chamada, JSON forçado por schema**, stateless (`gerar_json`),
  escolhido por `LLM_PROVIDER`. É o que `planejamento_ia.gerar_melhoria` e os dois
  pontos de `cenarios` usam; roda bem no 7B local.

Só transporte + parse + wrapping de erro — **zero lógica de domínio**: os prompts, os
schemas e a validação (`validar_diretrizes`) continuam nos services que os usam.

Degrada com segurança (princípio 6): qualquer falha (rede, timeout, JSON inválido,
provider desconhecido) vira `LLMIndisponivel`, e o caller entrega o plano base do
solver com `ia_indisponivel: true`. A IA nunca é caminho crítico.
"""

import json

import ollama
from django.conf import settings


class LLMIndisponivel(Exception):
    """LLM desligado/timeout/erro de rede/resposta não-parseável, ou provider
    mal configurado. Provider-neutro: substitui o antigo `OllamaIndisponivel`."""


# Alias de compatibilidade: `OllamaIndisponivel` era o nome levantado/capturado em
# `tasks.py` e importado por testes e por `cenarios`. É a MESMA classe — o `except`
# do job de cenários continua pegando o que `gerar_json` levanta.
OllamaIndisponivel = LLMIndisponivel

# Teto de tokens da resposta no path Anthropic. Diretrizes de planos grandes (uma
# prioridade e ajustes por tarefa) podem crescer; folgado de propósito.
_ANTHROPIC_MAX_TOKENS = 4096


class _OllamaProvider:
    """Cérebro local (Ollama). `format=schema` força JSON válido; temperature 0."""

    def gerar_json(self, *, system, messages, schema):
        cli = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT
        )
        resp = cli.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "system", "content": system}, *messages],
            format=schema,
            options={"temperature": 0},
        )
        return json.loads(resp["message"]["content"])


class _AnthropicProvider:
    """Cérebro remoto (API da Claude). A API não tem `format=`: o JSON estruturado
    sai de uma **tool forçada** cujo `input_schema` é o schema pedido — a resposta é
    o `tool_use.input`, já um dict. Habilita medir a IA por API (Fase 2)."""

    _TOOL_NAME = "responder"

    def __init__(self):
        try:
            import anthropic
        except ImportError as e:  # dep opcional (só quando LLM_PROVIDER=anthropic)
            raise LLMIndisponivel("pacote 'anthropic' não instalado") from e
        if not settings.ANTHROPIC_API_KEY:
            raise LLMIndisponivel("ANTHROPIC_API_KEY não configurada")
        if not settings.LLM_MODEL:
            raise LLMIndisponivel(
                "LLM_MODEL não configurada (obrigatória p/ anthropic)"
            )
        self._cli = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def gerar_json(self, *, system, messages, schema):
        resp = self._cli.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=_ANTHROPIC_MAX_TOKENS,
            temperature=0,
            system=system,
            messages=messages,
            tools=[
                {
                    "name": self._TOOL_NAME,
                    "description": "Responda EXCLUSIVAMENTE por esta ferramenta.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": self._TOOL_NAME},
        )
        for bloco in resp.content:
            if bloco.type == "tool_use":
                return dict(bloco.input or {})
        raise LLMIndisponivel("resposta sem tool_use")


class _MockProvider:
    """Sem rede: `LLM_PROVIDER=mock` faz a IA virar no-op determinístico. Devolve
    `{}` — melhoria cai em diretrizes vazias (o guarda-corpo aceita) e cenários/refino
    degradam para os arquétipos. Serve CI/demo sem Ollama; não substitui os mocks
    unitários das funções de alto nível."""

    def gerar_json(self, *, system, messages, schema):
        return {}


_PROVIDERS = {
    "ollama": _OllamaProvider,
    "anthropic": _AnthropicProvider,
    "mock": _MockProvider,
}


def _criar_provider():
    nome = (settings.LLM_PROVIDER or "ollama").lower()
    try:
        return _PROVIDERS[nome]()
    except KeyError:
        raise LLMIndisponivel(f"LLM_PROVIDER desconhecido: {nome}")


def gerar_json(*, system, messages, schema):
    """UMA chamada ao provider de `LLM_PROVIDER`, JSON forçado pelo `schema`.

    `system` é o prompt de sistema; `messages` é a lista de turnos (user/assistant/tool)
    já montada pelo caller; `schema` é o JSON Schema da resposta. Retorna o dict bruto
    (ainda a validar). Qualquer falha → `LLMIndisponivel`.
    """
    try:
        return _criar_provider().gerar_json(
            system=system, messages=messages, schema=schema
        )
    except LLMIndisponivel:
        raise
    except Exception as e:  # rede, timeout, JSON inválido, shape errado
        raise LLMIndisponivel(str(e))
