# Contexto — Fase 0A.1: abstração `LLMProvider`

> Fechamento da task (passo 7). O que era para fazer, o que foi feito, as decisões, e
> o estado em que a próxima começa. Plano em [`fase0a1-llmprovider.md`](fase0a1-llmprovider.md).

## O que era para fazer

Unificar os **3 pontos** que ainda instanciavam `ollama.Client` direto (a forma
*1 chamada + JSON schema*) atrás de uma abstração trocável por env — o mesmo padrão
que a Fase C já dera ao **agente** (`AGENTE_PROVIDER`), mas para a outra forma de
chamada. Default `ollama`: para quem roda local, nada muda.

## O que foi feito

- **Novo `planner/services/llm.py`** — dono da forma *chamada única*. `gerar_json(*,
  system, messages, schema) -> dict`, factory por `LLM_PROVIDER`, providers
  `_OllamaProvider` / `_AnthropicProvider` / `_MockProvider`. Só transporte + parse +
  wrapping de erro; zero lógica de domínio.
- **`planejamento_ia.gerar_melhoria`** e **`cenarios.gerar_cenarios_ia` /
  `refinar_cenario_ia`** encolheram: montam só system+messages+schema e chamam
  `llm.gerar_json`. `import ollama` saiu dos dois services.
- **`config/settings.py` + `.env.example`**: `LLM_PROVIDER` (default `ollama`) e
  `LLM_MODEL` (sem default; obrigatório no path anthropic).
- **Testes**: os 2 de baixo nível de `test_planejamento_ia.py` passaram a mockar a
  costura no lugar novo (`planner.services.llm.ollama.Client`); novo `test_llm.py`
  (8 testes) cobre factory, Ollama mockado, Mock, provider desconhecido, alias de
  compat e os guarda-corpos do path anthropic.

**Verde:** `285 passed`, `ruff` ok, `black --check` ok, sem migration nova.

## Decisões (o gate do passo 4)

- **D1 — 2 envs separados** (`AGENTE_PROVIDER` × `LLM_PROVIDER`), não unificados: são
  formas de propósito diferente (multi-turno stateful vs 1 chamada stateless) e
  necessidades diferentes (agente quer modelo forte remoto; planejador roda bem no 7B
  local). Só o vocabulário dos valores foi alinhado (`ollama|anthropic|mock`). Fecha a
  decisão em aberto que o ROADMAP listava.
- **D2 — Ollama + Anthropic + Mock**; **OpenAI adiado** (sem dependência nem uso hoje —
  YAGNI; entra se a Fase 2 decidir).
- **D3 — `OllamaIndisponivel` mantido como alias** de `LLMIndisponivel` (mesma classe).
  Era load-bearing: `tasks.py` levanta e captura `planejamento_ia.OllamaIndisponivel`
  nos 3 jobs, inclusive no job de cenários — o alias garante que o `except` continue
  pegando o que `gerar_json` levanta. Há teste explícito de identidade (`is`).
- **D4 — `LLM_MODEL` sem default, obrigatório** quando `LLM_PROVIDER=anthropic` (falha
  explícita, em vez de chutar um id de modelo que pode mudar).
- **D5 — interface stateless** (`gerar_json`), não classe-por-chamada: a forma é sem
  estado. O agente segue com provider instanciado por conversa porque lá há estado.

## Detalhes de desenho que podem surpreender

- **Anthropic não tem `format=`** (o `format` do Ollama força JSON pelo schema). No
  `_AnthropicProvider`, o schema vira o `input_schema` de **uma tool forçada**
  (`tool_choice`), e a resposta é o `tool_use.input` (já dict). É a assimetria que a
  abstração esconde — se um dia entrar OpenAI, será `response_format`, outra terceira
  forma.
- **`_MockProvider` devolve `{}`** de propósito: `LLM_PROVIDER=mock` faz a IA virar
  no-op determinístico. Melhoria cai em diretrizes vazias (o guarda-corpo
  `validar_diretrizes` aceita); cenários/refino degradam para os arquétipos. Serve
  CI/demo sem Ollama — **não** substitui os mocks unitários das funções de alto nível.
- **`temperature: 0`** nos 3 providers (Ollama via `options`, Anthropic via kwarg).

## Raio de impacto real

`services/llm.py` (novo), `services/planejamento_ia.py`, `services/cenarios.py`,
`config/settings.py`, `.env.example`, `tests/test_planejamento_ia.py` (2 testes),
`tests/test_llm.py` (novo). **Sem migration. Sem mudança de contrato HTTP** — a troca
é interna aos services; endpoints, request/response e status ficam idênticos.

## Estado em que a próxima task começa

- **Contrato externo inalterado** → o passo 8 é um prompt de "nada a fazer no frontend,
  eis o porquê" (como no PR1). O PR (passo 9) segue gated na confirmação do frontend.
- **Ponto ainda aberto do ROADMAP 0A**: 0A.2 (auto-pull + profiles do compose), 0A.3
  (instrumentação de tempo/tokens), 0A.4 (launcher), 0A.5 (matriz de modelos). A 0A.1
  não os toca.
- **PR2 (Supabase)** segue sendo a próxima task da ordem quando o projeto Supabase do
  usuário estiver pronto — roteiro na seção 8 de `contexto-0b-pr1.md`.
