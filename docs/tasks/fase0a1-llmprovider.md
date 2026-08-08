# Fase 0A.1 — Abstração `LLMProvider` (chamada única + JSON schema)

> **Plano (passo 3 do ciclo).** Escrito antes de codar, com as dúvidas explícitas.
> O passo 4 é um **gate**: nada de implementar antes de o usuário revisar e sanar as
> dúvidas. Task de encaixe enquanto o PR2 (Supabase) está bloqueado.

## 1. O que a task é (e o que não é)

Unificar os **3 pontos** que ainda instanciam `ollama.Client` direto atrás de uma
abstração trocável por env — o mesmo que a Fase C já fez para o **agente**, só que
para a outra forma de chamada. **Default `ollama`: para quem roda local, nada muda.**

Não é: mexer no solver, nos prompts, nos schemas, na validação de diretrizes, na
degradação (`ia_indisponivel`), nem no agente (`AGENTE_PROVIDER` continua como está —
ver Dúvida 1).

## 2. Estado atual (o que a análise achou)

Duas formas de chamar o LLM convivem hoje:

| Forma | Onde | Interface | Provider abstraído? |
| --- | --- | --- | --- |
| **Multi-turno + tool use** | `agente.py` | stateful (`gerar`/`responder_ferramentas`) | ✅ sim (`AGENTE_PROVIDER`) |
| **Chamada única + JSON schema** | `planejamento_ia.py`, `cenarios.py` (2×) | stateless (1 chamada → dict) | ❌ **não** — `ollama.Client` inline |

Os 3 pontos da segunda forma são **idênticos na mecânica**, só mudam prompt/mensagens/schema:

```python
cli = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT)
resp = cli.chat(model=settings.OLLAMA_MODEL, messages=[...], format=SCHEMA, options={"temperature": 0})
return json.loads(resp["message"]["content"])   # qualquer Exception → OllamaIndisponivel
```

- `gerar_melhoria(contexto)` — `planejamento_ia.py:195`
- `gerar_cenarios_ia(contexto)` — `cenarios.py:192` (extrai `.cenarios`)
- `refinar_cenario_ia(contexto, historico, mensagem)` — `cenarios.py:269`

**Quem chama:** `tasks.py:90` / `:178` / `:334` (os 3 jobs Celery). **Degradação:**
qualquer falha vira `OllamaIndisponivel` → o job entrega o plano base do solver com
`ia_indisponivel: true`. A IA nunca é caminho crítico (princípio 6).

**Testes que tocam nisto:**
- **Baixo nível (mockam `ollama.Client`)** — só **2**, em `test_planejamento_ia.py`
  (`test_gerar_melhoria_parse_ok`, `test_gerar_melhoria_erro_vira_indisponivel`).
  **Estes precisam mudar de costura** (ver §4).
- **Alto nível (mockam o *retorno* das 3 funções)** — `test_planejamento_ia.py`,
  `test_cenarios.py`, `test_refinar_cenario.py`, `test_tempos.py`. **Ficam verdes**:
  as funções mantêm nome e assinatura.

## 3. Desenho proposto

### 3.1 Novo módulo `planner/services/llm.py`

Dono da forma *chamada única*. Só transporte + parse + wrapping de erro — **zero
lógica de domínio** (prompts e schemas continuam em `planejamento_ia`/`cenarios`).

```python
class LLMIndisponivel(Exception): ...          # provider-neutro
OllamaIndisponivel = LLMIndisponivel           # alias de compat (ver Dúvida 3)

def gerar_json(*, system: str, messages: list[dict], schema: dict) -> dict:
    """1 chamada ao provider atual, JSON forçado pelo schema, temperature 0.
    Qualquer falha (rede, timeout, JSON inválido) → LLMIndisponivel."""
    return _criar_provider().gerar_json(system=system, messages=messages, schema=schema)
```

- `_OllamaProvider` — o corpo de hoje: `ollama.Client(...).chat(..., format=schema,
  options={"temperature": 0})` → `json.loads(...)`.
- `_AnthropicProvider` — **JSON via tool forçado**: a API da Claude não tem `format=`;
  o schema vira o `input_schema` de uma tool única com `tool_choice` forçado, e a
  resposta é o `tool_use.input` (dict pronto). Reusa `ANTHROPIC_API_KEY`. Habilita
  medir a IA por API na Fase 2 sem reescrever os call sites.
- `_MockProvider` — para `LLM_PROVIDER=mock`: devolve uma resposta benigna (diretrizes
  vazias) sem rede. Serve a CI/demo sem Ollama; **não** substitui os mocks unitários.
- `_criar_provider()` — factory por `settings.LLM_PROVIDER` (default `ollama`);
  provider desconhecido → `LLMIndisponivel` (mesma postura do `_criar_provider` do agente).

### 3.2 Os 3 call sites encolhem

Cada função perde o corpo `ollama.Client` e passa a montar só o que é seu (system +
messages + schema) e chamar `llm.gerar_json(...)`. Exemplo (`gerar_melhoria`):

```python
def gerar_melhoria(contexto):
    bruto = llm.gerar_json(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(contexto, ensure_ascii=False)}],
        schema=SCHEMA_MELHORIA,
    )
    return bruto
```

`import ollama` sai de `planejamento_ia.py` e `cenarios.py`. `cenarios.py` deixa de
importar `OllamaIndisponivel` de `planejamento_ia` e passa a importar de `llm`
(com o alias, o nome segue existindo).

### 3.3 Settings + `.env.example`

```python
LLM_PROVIDER = env("LLM_PROVIDER", default="ollama")   # ollama | anthropic | mock
LLM_MODEL = env("LLM_MODEL", default="")               # usado só no provider anthropic
```

Path ollama: reusa `OLLAMA_*` (inalterado). Path anthropic: `ANTHROPIC_API_KEY` +
`LLM_MODEL` (default a definir na Dúvida 4).

## 4. Testes

- **Reescrever os 2 de baixo nível** para a nova costura: mockar
  `planner.services.llm.ollama.Client` (mesma técnica, endereço novo), ou testar via
  `_MockProvider`. Mantêm o que provam hoje (parse ok; erro → indisponível).
- **Novos**, focados no `llm.py`: factory resolve por env; provider desconhecido →
  `LLMIndisponivel`; `_MockProvider` devolve dict; erro do cliente → `LLMIndisponivel`.
- Suíte + `ruff` + `black --check` + `makemigrations --check` (não há migration aqui).

## 5. Raio de impacto (arquivos)

| Arquivo | Mudança |
| --- | --- |
| `planner/services/llm.py` | **novo** — providers + factory + `gerar_json` |
| `planner/services/planejamento_ia.py` | corpo de `gerar_melhoria` → `llm.gerar_json`; remove `import ollama`; `OllamaIndisponivel` re-exportado de `llm` |
| `planner/services/cenarios.py` | idem nos 2 pontos; troca o import de `OllamaIndisponivel` |
| `config/settings.py` | `LLM_PROVIDER`, `LLM_MODEL` |
| `.env.example` | documenta as 2 vars |
| `planner/tests/test_planejamento_ia.py` | 2 testes de baixo nível → nova costura |
| `planner/tests/test_llm.py` | **novo** — factory, mock, erro |

Sem migration. Sem mudança de contrato HTTP (ver §7 — passo 8 será um prompt de
"nada a fazer no frontend, eis o porquê").

## 6. Dúvidas (o gate — resolver antes de codar)

**D1 — Unificar `AGENTE_PROVIDER` e `LLM_PROVIDER`, ou manter separados?**
(ROADMAP marca como decisão em aberto.) As duas formas são interfaces genuinamente
diferentes (multi-turno stateful vs 1 chamada stateless) e têm necessidades de
qualidade diferentes (o agente quer modelo forte remoto; o planejador roda bem no 7B
local). **Recomendo manter 2 envs**, mas alinhar o *vocabulário* dos valores
(`ollama|anthropic|mock` nos dois), em vez do `api|mock` que o texto do ROADMAP
sugeria — consistência sem acoplar as duas escolhas. *Alternativa:* um só
`LLM_PROVIDER` reger ambos (menos flexível: força agente e planejador no mesmo
provider).

**D2 — Escopo de providers agora.** Recomendo **Ollama (real, default) + Anthropic
(real) + Mock**, e **adiar OpenAI** (não há dependência nem uso; YAGNI — entra quando
a Fase 2 decidir). O ROADMAP lista OpenAI, daí a pergunta explícita.

**D3 — `OllamaIndisponivel`: renomear ou manter?** Recomendo introduzir
`LLMIndisponivel` (provider-neutro, honesto) e **manter `OllamaIndisponivel` como
alias** — evita churn em `tasks.py`/testes e o nome load-bearing continua válido.
*Alternativa:* rename completo (diff maior, mais honesto, mexe em mais arquivos).

**D4 — Modelo default do path Anthropic (`LLM_MODEL`).** O planejador não precisa de
modelo caro. Recomendo um **Haiku-class** como default (a Fase 2 ajusta com dado
real). Confirmar o id a usar, ou deixar `LLM_MODEL` **sem default** (obrigatório
quando `LLM_PROVIDER=anthropic`, falha explícita se faltar) — *recomendo esta
segunda*, mais honesta que chutar um id que pode mudar.

**D5 — Interface: função `gerar_json(system, messages, schema)` vs classe por
chamada.** Recomendo a **função stateless** (a forma é sem estado; mais fácil de
mockar). O agente segue com provider *instanciado* por conversa porque lá há estado —
são formas diferentes de propósito.

## 7. Passos 8–9 (frontend + PR)

**Sem mudança de contrato externo** — endpoints, request/response e códigos de status
ficam idênticos; a troca é interna aos services. O passo 8 será um prompt de "nada a
fazer no frontend, e por quê" (como foi no PR1), com o que rodar lá para confirmar que
nada quebrou. O PR (passo 9) segue gated na confirmação do frontend.
