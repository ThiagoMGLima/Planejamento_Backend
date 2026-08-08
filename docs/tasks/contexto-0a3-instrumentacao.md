# Contexto — Fase 0A.3, instrumentação das chamadas de IA

> **Contexto** (passo 7 do ciclo): o que era para fazer, o que foi feito, as decisões, os
> bugs encontrados e o estado em que a próxima task começa.
> Concluída em 08/08/2026. Plano: [`fase0a3-instrumentacao.md`](fase0a3-instrumentacao.md).

## O que era para fazer

O ROADMAP pedia *"logar tempo de parede real + (modo api) tokens"*. O objetivo real não é
o log: é dar à **Fase 2** ("IA local vs API") um dado para decidir, e à **0A.5** (matriz
3b×7b) algo para comparar. Task de encaixe, escolhida enquanto a 0B/PR2 esperava o
projeto Supabase.

O argumento de urgência foi de janela, não de dependência: o dogfooding começou, e cada
plano gerado sem instrumentação é uma amostra perdida para sempre.

## O que foi feito

| Arquivo | Mudança |
| --- | --- |
| `services/telemetria.py` | **novo** — `medir()` (context manager), `de_ollama()`, `de_anthropic()`, escrita JSONL |
| `services/llm.py` | providers devolvem `(dados, metadados)`; `gerar_json` ganhou `familia` (**obrigatório**) e `dono_id` |
| `services/agente.py` | os dois `_chamar()` medidos; `dono_id` atravessa `_criar_provider` até os providers |
| `services/planejamento_ia.py`, `services/cenarios.py` | repassam `familia` e `dono_id` |
| `tasks.py` | passa `dono_id` nos 3 call sites |
| `config/settings.py` | `TELEMETRIA_ENABLED`, `TELEMETRIA_JSONL` e o bloco **`LOGGING`** (não existia) |
| `tests/conftest.py` | fixture autouse que **desliga** a telemetria na suíte |
| `tests/test_telemetria.py` | **novo**, 11 testes |
| `tests/test_llm.py`, `tests/test_agente.py` | stubs ajustados às assinaturas novas |

Campos por registro: `ts`, `familia`, `provider`, `modelo`, `duracao_s`,
`tokens_entrada`, `tokens_saida`, `carga_s`, `geracao_s`, `tok_s`, `ok`, `erro`, `dono`.

## Achados da análise que mudaram o desenho

1. **`tempos.py` não servia** — mede o *job* (solver + DB + IA), guarda um escalar EWMA
   no cache com TTL de 30 dias. É estimador volátil para a UI, não histórico.
2. **O Ollama devolve tokens.** Medido, não suposto: `prompt_eval_count`, `eval_count` e
   durações em ns. O `(modo api)` do ROADMAP subestimava o que dá para coletar.
3. **`load_duration` era 78% de uma amostra real** (4,24s de 5,46s). Diluído no total, a
   0A.5 mediria cold start em vez de modelo.
4. **O agente não era medido por nada** — nem `tempos`, nem log. E é o caminho de N
   chamadas por turno.
5. **Não existia `LOGGING` no settings.** Um `logger.info` de módulo do app não aparecia
   em lugar nenhum (a raiz não tinha handler). Virou pré-requisito da própria task.

## Decisões (gate, resolvido com o usuário)

| # | Decisão |
| --- | --- |
| Q1 | **JSONL em volume**, não model no banco. Um 9º model-raiz traria FK `dono`, migration, guarda de escopo, admin e testes de isolamento — a máquina do PR1 para dado que não é de domínio. |
| Q2 | **Tokens também no modo local**, excedendo o texto do ROADMAP. Sem tok/s local, 0A.5 e 0A.6 não têm o que comparar. |
| Q3 | **`tempos.py` intocado.** Acoplar faria uma mudança de observabilidade quebrar a contagem regressiva do usuário. Docstring cruzada nos dois módulos. |
| Q4 | Agente **entra** na telemetria; **não** ganha família no `tempos` — seria feature de UX, mudaria contrato e puxaria os passos 8–9. |
| Q5 | **Falhas viram registro** (`ok=False` + **classe** da exceção, nunca a mensagem: mensagens ecoam payload). |
| Q6 | **`load_duration` separado** de `eval_duration`. |
| Q7 | **Console legível + JSONL estruturado** — públicos diferentes. Descartado `LOG_FORMAT` configurável (YAGNI, mesmo argumento que adiou o provider OpenAI na 0A.1). |
| Q8 | **Nunca registrar conteúdo, nem atrás de flag.** Flag de debug é ligada para investigar e nunca desligada; pelo princípio 9 é "convenção documentada", o degrau mais fraco. |
| Q9 | **Sem mudança de contrato HTTP.** |

### Por que `familia` é parâmetro obrigatório

Mesma razão pela qual o `dono` não virou `contextvar` na 0B.10: **identidade é parâmetro
do domínio, nunca ambiente.** Contexto implícito falharia em silêncio no worker Celery, e
um registro sem família não serve para nada. Com parâmetro obrigatório é `TypeError` na
primeira execução — o piso do princípio 9.

## Bugs encontrados durante a implementação

**A suíte escrevia no JSONL real.** Descoberto ao inspecionar o arquivo depois do primeiro
teste ponta a ponta: 11 das 12 linhas eram sintéticas (`gpt-caseiro`, providers sem chave,
timeouts forjados), vindas de `test_llm.py`. Como esse arquivo é a base da decisão da Fase
2, isso não é sujeira cosmética — é dado falso numa decisão de produto. Corrigido com
fixture autouse em `conftest.py` que desliga a telemetria; quem a testa religa apontando
para `tmp_path`.

**Falso alarme registrado para não se repetir:** o log de console parecia não funcionar
quando testado via `docker compose exec web python -c`. Não é bug — `python -c` não chama
`django.setup()`, que é quem aplica o `LOGGING`. Pelo caminho real (`manage.py shell`,
gunicorn, worker) o log sai. Use `manage.py shell -c` para testar logging.

## Verificação

- **296 testes** passando (285 antes + 11 novos), `ruff` e `black` limpos,
  `makemigrations --check` sem mudanças.
- **Ponta a ponta com Ollama real**, do container, arquivo aparecendo no host:

```json
{"carga_s": 0.1685, "dono": null, "duracao_s": 2.0751, "erro": null,
 "familia": "planejar_ia", "geracao_s": 1.6672, "modelo": "qwen2.5:3b-instruct",
 "ok": true, "provider": "ollama", "tok_s": 15.6, "tokens_entrada": 27,
 "tokens_saida": 26, "ts": "2026-08-08T18:43:28.753941+00:00"}
```

- Console, pelo caminho real:
  `INFO 15:44:03 planner.services.telemetria IA cenarios/ollama ok em 0.70s · 18→8 tok · 17.37 tok/s`

**Primeiro dado de referência (3b, CPU, modelo quente): ~15–17 tok/s.** Substitui a
estimativa solta de "~50s por plano" que o ROADMAP carregava, e é a linha de base contra a
qual a 0A.6 vai comparar a RX 7600.

## Como ler os dados

```bash
jq -s 'group_by(.provider + "/" + .modelo)[] |
  {chave: .[0].provider + "/" + .[0].modelo, n: length,
   tok_s: (map(.tok_s // empty) | add / length),
   falhas: (map(select(.ok == false)) | length)}' .telemetria/llm.jsonl
```

O arquivo é escrito pelo container como **root**; apagar de dentro (`docker compose exec
web rm -rf /app/.telemetria`) ou com `sudo`.

## Estado em que a próxima task começa

- A telemetria roda ligada por padrão e acumula dados a cada uso real. **Quanto mais tempo
  passar antes da 0A.5, melhor a matriz** — é o único item da 0A que fica melhor esperando.
- **A 0A.5 está destravada** e agora é medição, não impressão: falta trocar
  `OLLAMA_MODEL` para o 7b, gerar carga comparável e agregar o JSONL.
- **A 0A.6 tem linha de base** (~15–17 tok/s em CPU) para comparar com a RX 7600.
- **Nada mudou no contrato HTTP** — o frontend não tem o que fazer (passo 8).
- A 0B/PR2 segue sendo a próxima da espinha; o pré-requisito externo já foi cumprido
  (ver [`fase0b-pr2-supabase.md`](fase0b-pr2-supabase.md), Parte E executada).
