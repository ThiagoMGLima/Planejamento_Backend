# Contexto — Fase 1.1 / PR B: `aplicar_plano`

> **Documento de contexto (passo 7 do ciclo).** Escrito depois de implementar.
> Plano: [`fase1-parametros-por-tarefa.md`](fase1-parametros-por-tarefa.md).
> Anterior: [`contexto-fase1-pra.md`](contexto-fase1-pra.md).
> Data: 15/08/2026. Branch: `claude/fase1-pra-parametros-tarefa`.

## 1. O que era para fazer

> **B** — Ferramenta `aplicar_plano` (envolve `services/aplicacao.py`) +
> `a_partir_de`/`nao_antes_de` no caminho que persiste.

O buraco: `_simular_plano` aceitava `a_partir_de` e **não** persistia;
`_replanejar` persistia e **não** aceitava. Um plano montado com `a_partir_de`
não tinha como virar calendário, e `services/aplicacao.py` — que sabe persistir —
não estava exposto como ferramenta.

## 2. O que foi feito

| Arquivo | Mudança |
| --- | --- |
| `services/agente.py` | `_aplicar_plano` + registro em `FERRAMENTAS` (`muda_estado: True`) |
| `mcp_server/server.py` | tool `aplicar_plano` (encadeia calcular → aplicar); tupla de registro virou `TOOLS`, nomeada |
| `tests/test_agente_aplicar_plano.py` | **novo** — 11 testes |
| `tests/test_mcp_server.py` | +4 testes |

**Suíte: 338 → 353.** `ruff`, `black --check` e `makemigrations --check` limpos.
Container `mcp` rebuildado e verificado: 13 tools registradas.

## 3. Decisões de desenho

### 3.1 O plano não passa pelo modelo

A ferramenta **não** recebe uma lista de sessões. Recebe os mesmos argumentos
curtos de `simular_plano` (`tarefa_ids`, `preferencias`, `horizonte`,
`a_partir_de`) e **re-roda o solver por dentro**.

Duas razões, e as duas já estavam escritas no repositório antes deste PR:

- `_consultar_agenda`: *"o payload cru da API fazia o 7B alucinar o resumo […]
  entregar o resumo pronto reduz a tarefa do modelo a copiar"*. Fazer o LLM
  copiar de volta uma lista de sessões seria o mesmo erro, com o agravante de
  que aqui o resultado **grava no banco**.
- A view `/planejamento/replanejar/aplicar`: *"recalcular aqui dentro (em vez de
  confiar num plano enviado pelo cliente) evita aplicar plano obsoleto"*.

**Trade-off aceito e explícito:** a releitura pode divergir do que foi simulado
se a agenda mudou no meio. Por isso o retorno descreve o que foi **realmente
criado**, nunca o que se pretendia criar.

### 3.2 Idempotência de graça

`aplicar_sessoes` marca as tarefas como `PROMOVIDA`, e `validar_tarefas` recusa
tarefa promovida. Então a segunda chamada devolve
`422 {"motivo": "tarefa já promovida"}` em vez de duplicar eventos — sem
nenhuma trava nova. Coberto por teste, porque é a diferença entre "funciona" e
"o modelo chamou duas vezes e dobrou a agenda".

### 3.3 Saída digerida

Resumo agregado por tarefa (`{tarefa, sessoes, minutos, de, ate}`), não a lista
de eventos criados. É o que o modelo precisa narrar, e um teste trava o shape
para impedir que payload cru volte a vazar para o contexto.

### 3.4 `a_partir_de` no `replanejar`: **não** — e por quê

O plano dizia "`a_partir_de`/`nao_antes_de` no caminho que persiste". Entreguei
por dois caminhos diferentes do que a frase sugeria, e vale registrar:

- **`nao_antes_de`** já foi resolvido no **PR A**: virou campo da tarefa, que o
  solver honra em *todos* os caminhos — inclusive no replanejar (há teste).
- **`a_partir_de`** entrou no `aplicar_plano`, que é o par natural do
  `simular_plano`.

**Não** expus `a_partir_de` na ferramenta `_replanejar` do agente. Replanejar
significa "do agora em diante"; deixar o modelo escolher um "agora" arbitrário
numa ferramenta que **grava** congelaria ou descartaria sessões de forma difícil
de prever, para resolver um problema que o `nao_antes_de` por-tarefa já resolve
melhor (por tarefa, e não por chamada). O endpoint HTTP
`/planejamento/replanejar/aplicar` **já aceitava** `a_partir_de` desde o C2 —
quem precisar dele por fora continua tendo.

### 3.5 O MCP não podia ficar para trás

O `CLAUDE.md` afirma que as ferramentas do agente e as do MCP têm o mesmo
contrato — sem `aplicar_plano` lá, isso viraria mentira e o gap continuaria para
qualquer cliente MCP externo.

Como `/planejamento/aplicar` recebe **sessões**, a tool encadeia
`calcular` → `aplicar`. A lista de sessões trafega **dentro do servidor MCP**,
que é código, não modelo — o cliente MCP passa só os argumentos curtos. Mesma
propriedade da ferramenta em processo, por um caminho diferente.

Aproveitei para nomear a tupla de registro (`TOOLS`): definir a função e
esquecer de registrá-la falhava em silêncio, e agora há teste.

## 4. Estado em que a próxima task começa

**Pronto:** o ciclo completo simular → aplicar existe nas duas superfícies
(agente em processo e MCP), com `a_partir_de` chegando ao banco.

**PR C (próximo)** — nada disto existe ainda:

- a IA **não lê** a `descricao` da tarefa para inferir parâmetros;
- `criar_tarefa` **não aceita** os knobs do PR A (`estrategia`, `nao_antes_de`,
  …), então a IA cria tarefa que nasce sem estratégia;
- o plano **não devolve** `pergunta` (até 3, priorizadas — decisão D5);
- a **tabela de vocabulário** e o teste que barra UUID/nome de campo em texto
  para o usuário não foram escritos. O `SYSTEM_PROMPT` do agente **ainda não
  tem** a regra de linguagem que o prompt do planejador tem.

**PR D** segue bloqueado nas decisões **D6/D7**, ainda abertas.

## 5. Passo 8 — sincronia com o frontend

**Nada a fazer.** O PR B não muda contrato HTTP nenhum: não cria, remove nem
altera endpoint, serializer ou shape de resposta. A ferramenta nova vive dentro
do agente (chamada em processo) e do servidor MCP.

O que o frontend vê de diferente: um turno do agente pode agora responder com
`mudou_estado: true` por causa de `aplicar_plano` — comportamento que o front já
trata desde o C4 (é o sinal de recarregar o calendário), com um nome novo dentro
de `acoes[].ferramenta`. Se a UI listar as ações por nome, `aplicar_plano` é o
único rótulo novo.
