# Fase 1 — Parâmetros inteligentes por tarefa (estratégia, janela, observação)

> **Plano (passo 3 do ciclo).** Escrito antes de codar, com as dúvidas explícitas.
> O passo 4 é um **gate**: nada de implementar antes de o usuário revisar e sanar as
> dúvidas da seção 7.
>
> Origem: sessão de dogfooding de **15/08/2026** contra o banco real. Os números
> desta nota são **medições**, não estimativas — ver seção 2.

## 1. O que a task é (e o que não é)

Dar à `Tarefa` um punhado de **parâmetros de agendamento ocultos** (não aparecem no
frontend, não são digitados pelo usuário) e um campo de **observação em português**
que a IA traduz para esses parâmetros. O primeiro deles resolve um bug de produto já
observado: **estudo de prova sendo agendado dois meses antes da prova**.

Encaixa na **Fase 1** do `ROADMAP.md` ("Dogfooding + fechar regras de negócio"). É a
primeira regra de negócio fechada por uso real.

**Não é:** mexer em autenticação, no `dono`, no contrato do `/calcular`, nem na
degradação `ia_indisponivel`. **Não é** reescrever o solver — o núcleo guloso EDF
continua; ganha um sentido de varredura.

## 2. Estado atual (o que a análise achou)

### 2.1 O bug de produto

O solver é guloso EDF alocando **do `agora` para frente** — sempre o mais cedo
possível. Medido em 15/08/2026 com uma tarefa de 180 min cuja deadline é a PP1 de
EG1 (20/10/2026):

```
a_partir_de default (= agora, 15/08):
   2026-08-17 08:00 → 10:00   (120 min)
   2026-08-18 12:00 → 13:00   ( 60 min)      ← 2 meses antes da prova
```

O `a_partir_de` corrige, mas é um parâmetro de chamada, não uma propriedade da tarefa:

```
a_partir_de = 2026-10-19:
   2026-10-19 12:00 → 14:00
   2026-10-19 14:00 → 14:30
   2026-10-20 12:30 → 13:00   ← termina quando a prova começa
```

**E não há caminho para persistir isso.** As duas ferramentas do agente:

```python
# agente.py:225 — SIMULA, aceita a_partir_de, NÃO persiste
def _simular_plano(dono, tarefa_ids, preferencias=None, horizonte=None, a_partir_de=None)

# agente.py:247 — PERSISTE (aplicar=True), NÃO aceita a_partir_de
def _replanejar(dono, dias_bloqueados=None, preferencias=None, aplicar=False):
    agora = timezone.now()          # fixo
```

`services/aplicacao.py` e o endpoint `/planejamento/aplicar` existem, mas **não estão
expostos como ferramenta**. Nenhum prompt tapa esse buraco.

### 2.2 O que já existe e vai ser reusado

Boa parte da fundação está pronta — esta task é mais **extensão** que construção:

| Peça | Onde | Serve para |
| --- | --- | --- |
| Knobs por-tarefa (`prioridade`, `buffer_dias`, `max_min_por_dia`) | `planejamento.py:96` (`TarefaEntrada`) | molde exato dos campos novos; os defaults já "preservam o comportamento do solver puro" |
| `validar_diretrizes` — clamp/descarte, **nunca levanta** | `planejamento_ia.py:267` | guarda-corpo dos knobs novos, sem inventar mecanismo |
| Pipeline contexto → LLM (1 chamada, JSON schema) → validação → solver | `planejamento_ia.py` | a camada B é mais um fato na entrada e mais knobs na saída |
| `janela_por_dia` com validação `05:00 ≤ ini < fim ≤ 23:59` | `validar_diretrizes` | a janela **por-tarefa** copia a validação, muda o escopo |
| Grounding determinístico (classes e datas como FATOS) | `agente.py:584–607` | precedente da tabela de vocabulário (§3.5) |
| `AGENTE_PROVIDER` × `LLM_PROVIDER` separados | decisão 0A.1 no ROADMAP | o desenho de §3.6 já estava decidido; falta a ponte |
| `_AnthropicProvider` implementado | `agente.py:492–514` | conversa com modelo forte é config, não código |

### 2.3 O que o modelo local aguenta (medido em 15/08/2026)

Três turnos contra o agente, banco real, `qwen2.5:7b-instruct` na RX 7600:

| # | Pedido | Resultado |
| --- | --- | --- |
| 1 | Composto, em linguagem natural: "mais tempo de estudo para as provas que já estão no calendário, coladas nas provas" | ❌ **zero ferramentas** (telemetria: 1 chamada, 1487 tok entrada / 179 saída, 4,5 s). Inventou as disciplinas "Física Geral 1" e "Matemática Geral 1" (não existem; as reais são Física Experimental 2 e Física Teórica 4). Pediu ao usuário as datas das provas e ofereceu "usar as próximas segundas-feiras como referência". **Vazou um UUID na resposta ao usuário** (`classe_id: c9a351f9-…`). |
| 2 | "Consulte a agenda de 01–31/10 e diga quando é a prova de EG1" | ⚠️ Chamou `consultar_agenda` com argumentos corretos; a **ferramenta devolveu dado perfeito** (22 dias, PP1 em 20/10 terça 13:00–16:40). O modelo então **não respondeu a pergunta**, descartou 01–19/10 inteiro (incluindo a própria PP1) e **deslocou as datas em 3 dias** mantendo os nomes dos dias da semana. |
| 3 | Explícito e único: "crie a tarefa X, classe Estudar, deadline …, 180 min" | ✅ **Perfeito.** `criar_tarefa` com os 4 argumentos certos, persistido corretamente. |

**Leitura:** o 7b não falha em *executar*; falha em *entender e compor*. Um payload de
16 mil caracteres já o faz re-narrar de memória em vez de copiar.

**Subir de modelo local está fora:** a placa tem `8176 MiB` de VRAM. O 7b Q4 (4,7 GB)
cabe e roda a ~47 tok/s; um 14b Q4 (~9 GB) derrama para a CPU. "Modelo forte" aqui
significa, na prática, **remoto**.

### 2.4 A regra de vocabulário já existe — e já é violada

O prompt do planejador (`planejamento_ia.py:147`) manda:

> *"refira-se às tarefas SOMENTE pelo título, NUNCA escreva o id/UUID, e NUNCA cite
> nomes técnicos de campos como 'buffer_dias', 'max_min_por_dia' […] — descreva em
> linguagem natural"*

O `SYSTEM_PROMPT` do agente (`agente.py:382`) **não tem nada disso** — e foi exatamente
lá que o UUID vazou (teste 1). E prompt não basta: o mesmo prompt do planejador diz
"NUNCA invente números, horários ou datas", e o teste 2 deslocou a semana inteira.

## 3. Desenho proposto

Quatro camadas, com uma regra que atravessa todas: **o texto livre nunca é a fonte da
verdade; ele é traduzido para campos estruturados, e a tradução é auditável.**

### 3.1 Camada A — campos ocultos na `Tarefa`

> **Princípio que rege esta seção** (decidido no gate, 15/08/2026): **parâmetros
> ortogonais**. Cada knob expressa **uma** condição específica e não altera o
> significado de outro. Nada de acoplamento implícito — se duas condições precisam
> valer juntas, o usuário (ou a IA) liga as duas. **É aceito que isso resulte em
> vários parâmetros**; o custo de mais campos é menor que o de um campo cujo efeito
> muda conforme o vizinho.

Campos novos, ausentes do `mappers.js` do front (mas presentes na API — ver §7, D4):

| Campo | Tipo | Default | Dureza | Semântica |
| --- | --- | --- | --- | --- |
| `estrategia` | choice `CEDO` \| `TARDE` | `null` | — | `CEDO` = comportamento atual (o mais longe possível da deadline). `TARDE` = colar na deadline. `null` ⇒ trata como `CEDO`, mas **sem default herdado de classe** (D2). |
| `nao_antes_de` | `DateField` nulo | `null` | **dura** | Piso de data. É o `a_partir_de` virando propriedade da tarefa. |
| `nao_depois_de` | `DateField` nulo | `null` | **dura** | Teto de data. Espelho do anterior — é como se expressa "terminar na véspera" agora que `TARDE` não usa `buffer_dias` (D1). |
| `janela_inicio` / `janela_fim` | `TimeField` nulo | `null` | suave | Janela horária **só desta tarefa**; compõe com a global pelo mais restritivo. |
| `dias_permitidos` | `ArrayField(int)` nulo | `null` | suave | Dias da semana 0–6 aceitos. `null` = todos os que a preferência global liberar. |

**Dureza** define o que a cascata de relaxamento pode afrouxar. Os pisos/tetos de data
são **duros**: nunca relaxam, porque relaxá-los desfaz exatamente o pedido do usuário
("não antes de X" virando "antes de X" é um bug, não uma degradação). Janela e dias são
**suaves**: relaxam nos mesmos níveis dos equivalentes globais. Se um knob duro tornar
a tarefa infactível, ela cai em `nao_alocado` com motivo — não vira alocação errada.

Todos os defaults preservam byte a byte o comportamento atual — mesma disciplina do
comentário em `TarefaEntrada` (`planejamento.py:99`).

**Não há campo `observacao`:** o gate decidiu reusar `descricao` (D3). Ver §3.3.

### 3.2 Solver — a estratégia `TARDE`

O "mais cedo possível" mora em **dois pontos**, não está espalhado:

```python
# planejamento.py:298 — slots_livres
"""... Devolve em ordem cronológica."""

# planejamento.py:433 — _alocar
for s_ini, s_fim in slots:                      # primeiros que couberem
    ...
    fim = s_ini + timedelta(minutes=dur)        # ancora no INÍCIO do slot
```

`TARDE` = percorrer os slots **ao contrário** e ancorar no **fim** do slot
(`inicio = s_fim - dur`), com um `_snap_abaixo` espelhando o `_snap_acima` existente.

Três coisas continuam funcionando sem alteração:

- **Tetos diários** (`min_tarefa_dia`, `min_total_dia`) são dicionários — independem
  da ordem de varredura.
- **Cascata de relaxamento** (`NIVEIS`): cada nível re-roda `slots_livres` e aloca o
  restante, empurrando para mais cedo **só quando não coube**. É a semântica correta,
  de graça.
- **`nao_alocado`** segue reportando o que não coube antes da deadline.

**`TARDE` não usa `buffer_dias`** (D1). A ancoragem é contra a deadline **real**;
`buffer_dias` só tem efeito em tarefas `CEDO`. Quem quer "terminar na véspera" liga
`nao_depois_de` — uma condição explícita, não um efeito colateral de outra. É a
aplicação direta do princípio de §3.1.

**Colisão `CEDO`/`TARDE`** (D8): `ocupado` é mutado tarefa a tarefa, então a ordem de
processamento importa. Na prática as duas disputam extremos opostos da janela e
raramente colidem. **Decidido: manter o guloso e documentar**; a ordenação EDF continua
mandando (é ela que garante factibilidade), e `nao_alocado` já denuncia o que não coube.
Revisitar só se o uso real mostrar colisão.

### 3.3 Camada B — a observação em português

**Reusa o campo `descricao` que já existe** (D3), sem campo novo. A IA lê a descrição
**inteira** — sem marcador, sem sintaxe para decorar — e emite os knobs de §3.1 dentro
de `ajustes_por_tarefa`, a estrutura que **já existe**.

A contrapartida foi aceita conscientemente: `descricao` é visível e editável no front
(mapeada para `detalhes`), e as 41 tarefas atuais têm descrições escritas como nota
humana, que passam a ser lidas como possível instrução. **A mitigação é obrigatória e
não opcional: a IA SEMPRE reporta o que extraiu de cada descrição** ("li 'de manhã' ⇒
janela 08:00–12:00"), mesmo quando extraiu nada. Sem esse relato, uma má leitura vira
mudança silenciosa de agenda — e aí o campo teria sido um erro. Com ele, o texto que
dirige o planejamento é o mesmo que o usuário vê, o que é **mais** auditável que um
campo oculto, não menos.

`validar_diretrizes` ganha as regras novas:

- `estrategia`: só os literais `"CEDO"`/`"TARDE"`; qualquer outra coisa ⇒ descartada.
- `janela_inicio`/`janela_fim`: mesma validação do `janela_por_dia`
  (`05:00 ≤ ini < fim ≤ 23:59`), só que por-tarefa.
- `dias_permitidos`: inteiros 0–6, dedup; lista vazia ⇒ descartada (não existe tarefa
  sem dia possível).
- `nao_antes_de`: data ISO dentro do horizonte, e `≤ deadline`; fora ⇒ descartada.

E **nunca levanta**, como o resto da função.

### 3.4 A pergunta como dado, nunca como conversa

O `planejamento_ia` é **uma chamada, stateless** — decisão 0A.1 do ROADMAP. Ele não tem
canal para perguntar e esperar. Então a pergunta vira **campo do resultado**:

```
plano completo, com um default já aplicado
        +
pergunta: "Quer terminar o estudo um dia antes da prova,
           para não ficar estudando em cima da hora?"
```

Três propriedades que isso preserva:

1. **Nunca bloqueia.** O plano chega usável mesmo se a pergunta for ignorada —
   princípio 6, a IA nunca é caminho crítico.
2. **Cabe no contrato.** A resposta do job de IA já carrega `resumo`/`trade_offs`/
   `sugestoes`; `pergunta` é mais um campo. O `/calcular` (sem IA) não muda —
   `serializar_plano` fica intocado.
3. **Pergunta uma vez.** A resposta do usuário é gravada nos campos de §3.1. Na próxima
   vez a IA lê o campo em vez de perguntar de novo. O ciclo fecha na camada A.

**Até 3 perguntas por plano, priorizadas** (D5). O schema carrega uma ordem de impacto,
e o front mostra nessa ordem. Duas consequências que precisam estar no código, não só
aqui: cada pergunta é **ignorável isoladamente** (responder a 2ª sem responder a 1ª tem
de funcionar), e **toda pergunta tem um default já aplicado** no plano entregue — uma
pergunta sem default seria um plano incompleto esperando resposta, exatamente o que a
propriedade 1 proíbe. Se a IA emitir mais de 3, `validar_diretrizes` corta as excedentes
pela ordem de impacto.

### 3.5 Vocabulário — tabela, não prompt

Três medidas, em ordem crescente de garantia:

1. **Portar** a regra de `planejamento_ia.py:147` para o `SYSTEM_PROMPT` do agente.
   Barato, óbvio, insuficiente.
2. **Tabela de tradução determinística** — a frase que o usuário lê vem do código, não
   de paráfrase do modelo:

   | knob | frase |
   | --- | --- |
   | `buffer_dias: 1` | "termina na véspera" |
   | `estrategia: TARDE` | "estuda perto da prova" |
   | `janela 08:00–12:00` | "só de manhã" |

   Mesma filosofia do grounding determinístico de `agente.py:584`: copiar é confiável,
   parafrasear não é.
3. **Guarda de teste** — nenhum texto voltado ao usuário (`resposta`, `resumo`,
   `trade_offs`, `sugestoes`, `pergunta`) pode casar com regex de UUID nem conter
   `classe_id`, `tarefa_id`, `buffer_dias`, `max_min_por_dia`, `estrategia`,
   `nao_antes_de`. **Falha na suíte**, não convenção documentada — princípio 9, o mesmo
   raciocínio do manager que recusa query sem escopo.

### 3.6 Conversa forte → JSON único

O papel do modelo caro é **entender e convergir**, não executar. Quando a conversa
fecha, ela emite **um** JSON: exatamente os knobs de §3.1, por tarefa.

```
modelo forte (AGENTE_PROVIDER=anthropic)
    conversa, esclarece, pergunta o que faltar
        ↓  emite UM JSON
    { "<tarefa_id>": { "estrategia": "TARDE", "buffer_dias": 1, ... } }
        ↓
    validar_diretrizes  ← MESMO guarda-corpo, sem passe livre por ser modelo caro
        ↓
    solver (local, determinístico, 47 tok/s não entram na conta)
```

Isso tira do 7b exatamente o que ele erra (composição, §2.3) e deixa a execução para
código determinístico. Duas consequências que precisam estar escritas:

- **Privacidade (Dúvida 6).** Conversa remota = a agenda sai da máquina. O projeto é
  ciosa disso — a telemetria nunca grava conteúdo, nem atrás de flag. Mandar só a
  *conversa* para fora, mantendo o planejamento local, é o desenho mais barato e menos
  invasivo, mas ainda é decisão de produto.
- **Terceira via para a Fase 2.** O ROADMAP escreve a decisão da IA como binária
  ("local é viável? se não, API comercial"). O híbrido — **API para entender, local
  para calcular** — não está lá, e pelos dados de §2.3 é o mais promissor dos três.
  Vale virar item do ROADMAP.

### 3.7 Validação barata, antes de codar

Pôr `ANTHROPIC_API_KEY` no `.env`, `AGENTE_PROVIDER=anthropic`, e repetir **o teste 1
palavra por palavra**. Se um modelo forte encadeia `consultar_agenda` → N×
`criar_tarefa` sem alucinar, §3.6 está validada com dado real. Se falhar, o gargalo é
de ferramenta (payload grande, falta do `aplicar_plano`) e a prioridade da seção 4
muda. Custa uma chave e cinco minutos, e vale mais que qualquer estimativa.

## 4. Corte em PRs

"1 task = 1 PR" (CLAUDE.md). O escopo acima é grande demais para um; proposta de corte
— cada linha é entregável e testável sozinha:

| PR | Escopo | Depende de |
| --- | --- | --- |
| **A** | Campos de §3.1 + `TarefaEntrada` + `estrategia TARDE` no solver + validação. **Sem IA nenhuma.** | — |
| **B** | Ferramenta `aplicar_plano` (envolve `services/aplicacao.py`) + `a_partir_de`/`nao_antes_de` no caminho que persiste | A |
| **C** | Camada B (`descricao` → knobs + leitura reportada), `pergunta` no resultado, tabela de vocabulário + guarda de teste | A |
| **D** | Handoff da conversa forte (§3.6) | B, C + **D6/D7 decididas** + resultado de §3.7 |

> ⚠️ **Consequência da D2 sobre o corte.** O plano original dizia "o PR A sozinho já
> resolve o bug de §2.1". **Isso deixou de ser verdade** quando o gate decidiu que não
> há default por classe: o PR A entrega a *capacidade* (`TARDE` funciona), mas nada
> **liga** o campo — o frontend não mostra, e a IA só entra no PR C. Entre A e C, quem
> seta é chamada de API, admin, ou um backfill pontual.
>
> Encaminhamento proposto, coerente com "só explícito na criação" (D2): fechar o PR A
> com um **comando de migração de dados opcional** (`manage.py`, não migration
> automática) que marca `estrategia=TARDE` nas tarefas de estudo já existentes, listando
> o que vai mudar antes de aplicar. Cada tarefa fica com valor **explícito e próprio** —
> nenhum default herdado —, e a agenda real é corrigida sem esperar o PR C.
> Ver Pendência 1 em §7.

O PR A funciona com `IA_PLANEJAMENTO_ENABLED=0`.

## 5. Testes

A suíte tem 296. Estimativa de ~20 novos:

- **`TARDE` puro:** tarefa única, sessões coladas na deadline; verificar ancoragem no
  fim do slot e o snap para baixo.
- **`TARDE` + relaxamento:** esforço que não cabe na janela colada ⇒ a cascata empurra
  para mais cedo, e só o necessário.
- **`TARDE` + `buffer_dias`:** termina antes da deadline efetiva (Dúvida 1).
- **Mistura `CEDO`/`TARDE`** no mesmo plano, com `ocupado` compartilhado.
- **Janela por-tarefa:** compõe com a global pelo mais restritivo; janela impossível
  cai em `nao_alocado` com motivo, não em exceção.
- **`nao_antes_de`** posterior à deadline ⇒ descartado pela validação, não quebra.
- **Defaults preservam o comportamento:** rodar um plano existente com todos os campos
  nulos e comparar sessão a sessão com o resultado atual.
- **`validar_diretrizes`:** um caso de lixo por knob novo (tipo errado, fora de faixa,
  id inexistente) — todos descartados, nunca levanta.
- **Vocabulário:** regex de UUID e de nomes de campo sobre todos os textos de saída.

Lembrar: os testes exigem Postgres (`ArrayField`), e a telemetria fica desligada pela
fixture autouse de `tests/conftest.py`.

## 6. Raio de impacto (arquivos)

| Arquivo | Mudança |
| --- | --- |
| `planner/models.py` | 6 campos novos em `Tarefa` |
| `planner/migrations/00XX_*.py` | migration dos campos (todos nulos/default ⇒ sem backfill) |
| `planner/services/planejamento.py` | `TarefaEntrada` + `slots_livres`/`_alocar` com sentido de varredura + `_snap_abaixo` |
| `planner/services/planejamento_ia.py` | schema + `validar_diretrizes` + `construir_contexto` (observação) + `pergunta` |
| `planner/services/agente.py` | `SYSTEM_PROMPT` (vocabulário), ferramenta `aplicar_plano`, tabela de tradução |
| `planner/serializers.py` | campos novos **entram** no serializer (D4); ocultar é papel do `mappers.js` no front |
| `planner/tests/` | ~20 testes novos |
| `ROADMAP.md` | itens novos na Fase 1 + a terceira via na Fase 2 |

## 7. Gate — respondido em 15/08/2026

### Decidido

| # | Pergunta | Decisão |
| --- | --- | --- |
| **D1** | `TARDE` respeita `buffer_dias`? | **Não.** `TARDE` ancora na deadline real; `buffer_dias` só vale para `CEDO`. "Terminar na véspera" passa a ser `nao_depois_de`, condição própria. |
| **D2** | Default de `estrategia` por classe? | **Não há default.** Só o que for explícito na tarefa. Sem herança de classe, nem na criação nem em tempo de plano. |
| **D3** | Campo `observacao` novo? | **Não** — reusa `descricao`. A IA lê o **texto inteiro** (sem marcador) e **sempre reporta o que extraiu**. |
| **D4** | Campos ocultos saem na API? | **Sim, no serializer**, ausentes do `mappers.js`. Decisão minha, ver nota abaixo. |
| **D5** | Quantas perguntas por plano? | **Até 3, priorizadas** por impacto. Cada uma ignorável isoladamente, cada uma com default já aplicado. |
| **D8** | Colisão `CEDO`/`TARDE` no guloso? | **Manter guloso + documentar.** EDF continua mandando; revisitar só se o uso mostrar colisão. |

**Princípio geral que saiu da D1** e rege todo o desenho: **parâmetros ortogonais**.
Cada knob é uma condição específica, sem acoplamento implícito com os outros. Aceita-se
que isso gere vários parâmetros — ver a nota em §3.1.

**Nota sobre a D4** (usuário respondeu "não tenho certeza"; decidido por mim como
detalhe técnico, aberto a revisão): os campos ficam **no serializer**, ausentes do
`mappers.js` do front. Três razões: (a) é o que a D3 já implica — `descricao` é o
principal deles e sempre esteve na API; (b) deixa o agente e o MCP lerem e escreverem
pelo caminho normal, sem service em processo; (c) menos código para o mesmo efeito
prático, já que "oculto" aqui significa "o usuário não digita", não "segredo".

### Aberto — decidir com calma, **bloqueia só o PR D**

| # | Pergunta | Estado |
| --- | --- | --- |
| **D6** | A conversa pode rodar em modelo remoto, mandando a agenda para fora? | **Em aberto.** É decisão de produto, não técnica — ver "Decisões em aberto" no `ROADMAP.md`. |
| **D7** | Onde vive o JSON único: ferramenta `definir_parametros` ou endpoint novo? | **Em aberto, e a jusante da D6** — se a D6 for "só provider local", o handoff da §3.6 perde sentido e a D7 evapora. Decidir as duas juntas, nessa ordem. |

**Os PRs A, B e C não dependem disso** e podem andar já. A validação de §3.7 (rodar o
teste 1 com provider forte) é o que dá dado para a D6 — mas ela mesma exige aceitar o
envio uma vez, então nem esse teste é neutro. Vale decidir a D6 no princípio, não pelo
resultado do teste.

### Pendências abertas por estas respostas

1. **Quem liga `estrategia=TARDE` entre o PR A e o PR C?** A D2 tirou o default, o front
   não mostra o campo e a IA só chega no PR C. Proposta em §4: comando `manage.py`
   opcional, que lista antes de aplicar e grava valor explícito por tarefa. **Confirmar
   no início do PR A.**
2. **`criar_tarefa` (ferramenta do agente) precisa aceitar os knobs novos** — senão, com
   a D2, a IA cria tarefas que nunca terão estratégia. Entra no PR C, não no A.

## 8. Passos 8–9 (frontend + PR)

**Passo 8 — prompt de sincronia.** O PR A **não muda o contrato externo**: campos
ocultos, sem alteração em `serializar_plano` nem nas rotas. O prompt para o agente do
frontend será do tipo "nada a fazer, eis a razão", com o roteiro de confirmação
(rodar os E2E, conferir que o calendário não mudou de forma).

Os PRs C e D **mudam** a resposta do job de IA (campo `pergunta`) — aí o prompt de
sincronia descreve o campo novo, quando ele aparece e como o front deve exibi-lo
(chip/pergunta opcional que **não bloqueia** o plano).

**Passo 9 — gate.** PR só depois de o usuário aplicar o lado do frontend, rodar os E2E
e confirmar verde.
