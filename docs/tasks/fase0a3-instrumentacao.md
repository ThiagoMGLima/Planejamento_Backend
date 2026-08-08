# Fase 0A.3 — Instrumentação das chamadas de IA

> **Plano** (passo 3 do ciclo). Escrito antes de qualquer código. As dúvidas da
> seção "Gate" precisam ser resolvidas antes do passo 5.
> Data: 08/08/2026. Task de encaixe, enquanto a 0B/PR2 aguarda o projeto Supabase.

## Por que agora

O ROADMAP pede *"logar tempo de parede real + (modo api) tokens"*. O valor não é o
log em si — é a **Fase 2** ("IA local vs API"), que hoje decidiria no escuro: o único
dado que existe é uma linha do ROADMAP dizendo que o `qwen2.5:3b-instruct` fechou um
plano em ~50s em CPU, colhida em teste solto.

Há uma janela: o uso pessoal (Fase 1, dogfooding) **começou agora**. Cada plano gerado
daqui pra frente é uma amostra. Sem instrumentação, essas amostras não existem — não é
trabalho adiado, é dado perdido. Isso é o que põe a 0A.3 na frente da 0A.2/0A.4.

A 0A.5 (matriz 3b × 7b) também depende disto: sem medir, "comparar" vira impressão.

## Análise do código atual

### O que já existe — e por que não serve

`services/tempos.py` mede tempo, mas **não é registro; é estimador**:

| | `tempos.py` (existe) | 0A.3 (falta) |
| --- | --- | --- |
| Unidade medida | o **job inteiro** (solver + DB + serialização + IA) | a **chamada de IA** isolada |
| Granularidade | 1 escalar por família | 1 registro por chamada |
| Onde vive | `cache` (Redis), TTL 30 dias | a definir — ver Q1 |
| Para quê | contagem regressiva do front | decidir local vs API (Fase 2) |
| Tokens | nenhum | o ponto |

`tempos.registrar` é chamado em 3 lugares (`tasks.py:112, 225, 388`) e guarda a razão
real/prevista via EWMA. É um número derivado e volátil: um `docker compose down -v`, ou
30 dias parado, e ele volta à semente. Serve ao propósito dele; não serve como histórico.

### O que é descartado hoje

`services/llm.py:54` — `return json.loads(resp["message"]["content"])`. Todo o resto da
resposta vai no lixo. Idem nos dois `_chamar()` de `services/agente.py` (linhas 445 e 509).

**Medido hoje** (`qwen2.5:3b-instruct`, CPU, container do compose), o Ollama devolve:

```
total_duration        5460048910   (ns)
load_duration         4238629226   (ns)  ← 78% do total: modelo frio
prompt_eval_count     35           (tokens de entrada)
prompt_eval_duration  663082000    (ns)
eval_count            9            (tokens de saída)
eval_duration         550996000    (ns)
done_reason           stop
```

Dois achados que mudam o desenho:

1. **Tokens existem no modo local.** O `(modo api)` do ROADMAP subestima o que dá para
   medir — ver Q2.
2. **`load_duration` precisa ser separado.** Nessa amostra, 4,24s dos 5,46s foram carga
   do modelo, não inferência. Somar tudo mediria o cold start, não o modelo — e a matriz
   da 0A.5 sairia errada. Com `OLLAMA_KEEP_ALIVE=-1` o normal é ~0, mas exatamente por
   isso a exceção precisa ser visível em vez de diluída.

   *(Dessa amostra: 9 tokens / 0,551s ≈ **16 tok/s** de geração no 3b em CPU. Ponto solto,
   entra aqui só como ordem de grandeza.)*

### O agente não é medido

`agente_chat_task` (`tasks.py:377`) não chama `tempos.registrar` e não tem família. É o
caminho de **N chamadas por turno** (`MAX_ITERACOES = 6`) — o mais caro em tokens e o
candidato mais provável a virar API. É o que hoje tem menos dado.

### Não há configuração de logging

`grep -n "LOGGING" config/settings.py` → nada. Só `services/holidays.py` usa `logger`, e
apenas em `warning`. Sem `LOGGING` no settings, o logger de um módulo do app propaga para
a raiz, que não tem handler — **um `logger.info` não apareceria em lugar nenhum**. Então a
0A.3 tem um pré-requisito próprio: configurar logging. Ver Q7.

### Costuras disponíveis

Duas, e só duas:

- `services/llm.py::gerar_json` — um ponto cobre os **três** call sites de 1 chamada
  (`planejamento_ia.gerar_melhoria`, `cenarios.gerar_cenarios_ia`, `cenarios.refinar_cenario_ia`).
- `services/agente.py::_OllamaProvider._chamar` / `_AnthropicProvider._chamar` — dois
  pontos, N chamadas por turno.

Nenhum service precisa mudar de assinatura. Instrumentar não toca views, serializers,
models nem migrations.

## Desenho proposto

Um módulo novo, `services/telemetria.py`, com uma função só:

```python
registrar_chamada(
    familia,        # planejar_ia | cenarios | refino | agente
    provider,       # ollama | anthropic | mock
    modelo,
    duracao_s,      # parede, medida no cliente
    tokens_entrada=None,
    tokens_saida=None,
    carga_s=None,   # load_duration; só Ollama
    geracao_s=None, # eval_duration; permite tok/s honesto
    ok=True,
    erro=None,      # classe da exceção, nunca a mensagem
    dono_id=None,
)
```

Os dois call sites viram um `try/finally` fino em volta da chamada existente. A
normalização dos metadados (ns → s, nomes distintos por provider) mora na telemetria,
não nos providers.

**Nunca entra no registro:** prompt, `contexto`, títulos de tarefas, diretrizes,
resposta do modelo. Só contagens, durações e identificadores. O `construir_contexto`
carrega a agenda inteira do usuário; o beta vai rodar na máquina de amigos. Ver Q8.

**Regra de ouro:** a telemetria **nunca** pode derrubar uma chamada de IA. Falha ao
registrar é engolida com `logger.warning`. Mesmo espírito do `validar_diretrizes`, que
nunca levanta (princípio 6).

## Gate — dúvidas a resolver antes de codar

| # | Dúvida | Recomendação |
| --- | --- | --- |
| **Q1** | **Onde o dado vive?** (a) só log estruturado; (b) log + arquivo JSONL append-only em volume; (c) log + model no banco | **(b)**. (a) é fiel ao ROADMAP mas `docker compose logs` não é agregável nem sobrevive a `down -v` — a Fase 2 quer somar semanas. (c) seria o 9º model-raiz: FK `dono`, migration, admin, `sem_escopo`, testes de isolamento — máquina pesada demais para telemetria, e telemetria não é dado de domínio. (b) é durável, some com `jq`/pandas, zero schema. |
| **Q2** | **Tokens no modo local também**, contrariando o `(modo api)` do ROADMAP? | **Sim.** Está medido acima: o Ollama devolve. Sem token local não há tok/s, e sem tok/s a 0A.5 (3b×7b) e a 0A.6 (CPU×RX 7600) não têm o que comparar. Se você preferir seguir o texto à risca, digo — mas perde-se justamente o número que decide a Fase 2. |
| **Q3** | **`tempos.py` fica intocado?** | **Sim.** São coisas diferentes (tabela acima) e a tentação de "unificar" é real. Proponho anotar isso no docstring dos dois módulos para o próximo agente não fundir. |
| **Q4** | **O agente entra?** E ganha família no `tempos` (contagem regressiva no front)? | **Entra na telemetria** — é o caminho mais caro e o menos medido. **Não ganha família no `tempos`**: isso é feature de UX, muda contrato e puxa sincronia com o frontend. Vira item separado se você quiser. |
| **Q5** | **Falhas viram registro?** | **Sim** — `ok=False` + classe do erro. Taxa de timeout e de indisponibilidade decide local-vs-API tanto quanto latência. Hoje `LLMIndisponivel` é engolido e some. |
| **Q6** | **Separar `load_duration`?** | **Sim** — ver o achado acima (78% da amostra). Sem separar, mede-se o cold start. |
| **Q7** | **Formato do log.** Texto legível ou JSON? | Adicionar um `LOGGING` mínimo no settings (não existe hoje) com **texto legível no console** e o registro estruturado indo para o JSONL do Q1. Log é para você acompanhar; JSONL é para agregar. Alternativa: env `LOG_FORMAT=texto\|json`. |
| **Q8** | **Confirma que nenhum conteúdo é registrado?** | Confirmo como regra do desenho. Vale seu aval explícito, porque é fácil alguém "só adicionar o prompt para depurar" depois. |
| **Q9** | **Muda contrato HTTP?** | **Não.** Nada de novo no payload dos jobs. Consequência boa: o passo 8 do ciclo vira "nada a fazer no frontend, eis o porquê" e o gate do passo 9 fica trivial. Se você quiser os números visíveis na UI, é outra task. |

## Testes previstos

- `gerar_json` registra: duração, tokens de entrada/saída, provider e modelo — nos três
  providers (`ollama` mockado, `anthropic` mockado, `mock`).
- Falha do provider registra `ok=False` **e** a exceção continua propagando (o caller
  precisa seguir degradando para `ia_indisponivel: true`).
- Telemetria quebrada não derruba a chamada de IA (injetar exceção no registrador).
- Nada de conteúdo no registro: asserção de que prompt e resposta não aparecem.
- O agente registra **N** chamadas num turno com tool use, não uma.
- `tempos.py` continua se comportando igual (a suíte de `test_tempos.py` deve passar sem
  alteração — é o sinal de que Q3 foi respeitado).

## Fora de escopo

- Model no banco / dashboard / endpoint de métricas.
- Família de `tempos` para o agente (Q4).
- Qualquer mudança de contrato HTTP.
- Rodar a matriz 3b×7b — isso é a **0A.5**, que esta task habilita.
