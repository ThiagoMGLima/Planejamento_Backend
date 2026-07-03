# Visão — Rotina inteligente (cenários, adaptação e agente)

> **Status: visão consolidada do brainstorm (2026-07).** Formaliza a evolução do
> planejador para além do Marco 6: cenários com trade-offs, replanejamento,
> aprendizado do comportamento do usuário e agente conversacional.
>
> Princípio mantido de toda a Fase A, agora elevado a lei do projeto:
> **o solver é a fonte de verdade e garante a validade; a IA opera só na camada
> de intenção (propõe diretrizes), e todo número que chega ao usuário vem do
> código, nunca da IA.** A decisão de manter o backend próprio (em vez de
> agente + MCP direto no Notion) decorre disso: CRUD conversacional qualquer
> integração dá; o solver, as métricas e o histórico de execução são o produto.

## 1. Arquitetura em três camadas (onde entra inteligência, e onde não)

```
┌──────────────────────────────────────────────────────────────────┐
│ CAMADA DE CONVERSA (Marco C3) — agente com tool use              │
│ "adiciona trabalho de Física 2 pra sexta" / "e se eu estudar     │
│ cálculo na quinta?" → chama ferramentas da API (via MCP)         │
├──────────────────────────────────────────────────────────────────┤
│ CAMADA DE INTENÇÃO — IA propõe (nunca decide)                    │
│ diretrizes e CENÁRIOS candidatos a partir de fatos grounded;     │
│ guarda-corpo clampa/descarta; propostas ruins morrem no filtro   │
├──────────────────────────────────────────────────────────────────┤
│ CAMADA DE VERDADE — determinística                               │
│ solver (montar_plano, puro) + métricas + filtro de dominância +  │
│ histórico de execução → planos válidos por construção            │
└──────────────────────────────────────────────────────────────────┘
```

O insight que sustenta a camada do meio: como `montar_plano` é **puro e barato
(ms)**, a IA não precisa acertar — precisa só ser **plausível**. Propostas são
todas executadas pelo solver e medidas pelo código; as ruins são descartadas
antes de chegarem ao usuário (**generate-and-test**). Isso rebaixa a exigência
sobre o modelo (o 7B local dá conta) e mantém o grounding: a IA escolhe *quais
knobs mexer e com quais valores* dado o contexto (onde está o pico, qual
deadline aperta, qual dia é recuperável) — que é a parte volátil e situacional
— mas o resultado exibido é sempre medição do solver.

## 2. Marco C1 — Cenários com trade-offs ("trabalhe até 20h na quinta e ganhe o sábado")

A feature central da visão: em vez de um único plano melhorado, o sistema
apresenta **3–4 cenários comparáveis**, cada um com métricas e uma narrativa do
trade-off, para o usuário escolher.

### 2.1 Pré-requisito: expandir o vocabulário de diretrizes

As diretrizes atuais só **apertam** (prioridades, `buffer_dias`, tetos; o
`max_min_por_dia_total` deliberadamente nunca afrouxa). O cenário matador exige
alavancas novas no solver, todas com guarda-corpo próprio em
`validar_diretrizes` (clamp/descarte, nunca levanta):

| Alavanca nova                | Expressa                                    | Guarda-corpo                                   |
| ---------------------------- | ------------------------------------------- | ---------------------------------------------- |
| `janela_por_dia[semana/data]`| "até 20h na quinta" (janela por dia)        | dentro de 05:00–23:59; datas no horizonte      |
| `usar_fds` (global/por data) | liberar fim de semana como ESCOLHA          | bool; por data só dentro do horizonte          |
| `dias_bloqueados[]`          | "sábado livre" como restrição do cenário    | ≤ N dias; nunca inviabilizar deadline (solver relaxa se precisar) |

Nota de semântica: dentro de um **cenário**, encolher/estender a janela do
usuário é permitido — diferente da melhoria única de hoje — porque o cenário é
uma proposta explícita que o usuário vê e aceita, não um ajuste silencioso.
No solver, essas alavancas entram como overrides por-dia em `slots_livres`
(hoje a janela é única para todos os dias); a cascata de relaxamento continua
por cima, garantindo factibilidade.

### 2.2 Pipeline

```
POST /planejamento/cenarios  (tarefa_ids, prefs, horizonte) → 202 {job_id}
  1. solver monta o PLANO BASE (referência de comparação)
  2. construir_contexto (fatos: carga_por_dia, picos, capacidade, deadlines)
  3. ARQUÉTIPOS por código (sempre): base | espalhado (teto total ↓)
     | intenso (janela ↑ dias úteis, fds bloqueado) | frente-carregada (buffers ↑)
  4. IA (1 chamada, schema JSON) → array de 3–6 cenários candidatos,
     cada um {nome, intencao, diretrizes} — personalizados pela situação
  5. guarda-corpo valida cada cenário (clamp/descarte por alavanca)
  6. solver roda TODOS (arquétipos + IA) — ms cada
  7. MÉTRICAS por código (§2.3) + FILTRO DE DOMINÂNCIA: cenário pior ou igual
     em todas as métricas que outro → descartado; dedup de planos idênticos
  8. top 3–4 (base sempre incluso) + narrativa dos trade-offs
GET /planejamento/cenarios/{job_id} → {cenarios: [{nome, plano, metricas,
     trade_offs}], ia_indisponivel?}
```

Degradação (mesma filosofia do planejar-ia): Ollama fora → só os arquétipos de
código, `ia_indisponivel: true`. Cache no Redis pela mesma família de chave.

### 2.3 Métricas (código, comparáveis entre cenários)

- `pico_min_dia` — carga do dia mais pesado;
- `dias_livres` — dias sem nenhuma sessão (fds destacado);
- `folga_media_h` — média de (deadline − fim da última sessão da tarefa);
- `min_fora_janela` — minutos alocados fora da janela preferida do usuário;
- `fragmentacao` — nº de sessões / nº de tarefas;
- `nao_alocado_min` — o que não coube (métrica dominante: cenário que aloca
  menos que o base nunca sobrevive ao filtro).

A narrativa do trade-off ("estendendo quinta até 20h, o sábado fica livre")
é gerada a partir do **diff de métricas contra o base** — template de código
primeiro; segunda chamada de IA para polir o texto é opcional e adiável.

## 3. Marco C2 — Replanejar a partir de agora (emergências)

`POST /planejamento/replanejar`: congela o passado, devolve ao pool o esforço
das sessões perdidas/restantes (aproveita `remarcar`, que já devolve a Tarefa
ao Inbox), re-roda o solver só do `agora` em diante e responde com **plano
novo + diff** contra o aplicado ("Cálculo: qua→qui; sábado ganhou 1h").
Variante "hoje não" (cansaço): `dias_bloqueados=[hoje]` — é um cenário C1 de
um knob só, com o custo explicitado pelas métricas ("nada atrasa" ou "quinta
fica com 5h"). Nada disso usa IA.

## 4. Marco C3 — Registro de execução + fatores adaptativos

Fundação do "ele aprende quanto tempo você leva na academia". Coletar cedo:

- **Model `RegistroExecucao`**: (tarefa/ocorrência, planejado_min, real_min,
  concluida_em, n_remarcacoes). Escrito pelos fluxos `concluir`/`remarcar`
  (real_min informado pelo usuário ou inferido; opcional no MVP).
- **Fator de estimativa por classe** (estatística simples, sem ML): EWMA de
  `real/estimado` → solver usa `esforco × fator_classe`; exposto na UI
  ("Cálculo costuma levar 1.3× o que você estima").
- **Score de flexibilidade por classe**: taxa de remarcação → classes elásticas
  viram amortecedor preferencial do replanejamento (C2) e dos cenários (C1);
  as rígidas (aula, estágio) ficam intocadas.
- Ambos os fatores entram como **fatos no `construir_contexto`** — a IA passa a
  propor cenários sabendo o comportamento real do usuário, sem inventar nada.

## 5. Marco C4 — Servidor MCP + agente conversacional

- **MCP server fino sobre a API existente** (ferramentas: `criar_tarefa`,
  `listar_pendentes`, `simular_plano` [what-if, não persiste — é só
  `montar_plano` com entradas hipotéticas], `gerar_cenarios`, `aplicar_plano`,
  `remarcar`, `replanejar`). Torna o backend usável por **qualquer** runtime de
  agente (Claude, Hermes, etc.) — o framework do agente é a parte trocável; a
  camada de ferramentas é o investimento durável.
- O agente cobre: adicionar tarefas por linguagem natural, perguntas what-if
  ("se eu fizer Física 2 e estudar Cálculo na quinta, como fica?" →
  `simular_plano` + diff), e disparar C1/C2.
- Realismo de hardware: o 7B/CPU serve para as chamadas únicas com schema
  (diretrizes, cenários), mas agência multi-turno com tool use pede modelo
  maior — deixar o endpoint do **agente** configurável (Ollama maior/GPU ou API
  remota), mantendo solver, diretrizes e dados 100% locais.
- Integrações (Notion/Google Calendar) entram aqui como **views sincronizadas**
  do plano aplicado, nunca como fonte de verdade.

## 6. Ordem e dependências

```
C1 cenários  ──► C2 replanejar (reusa métricas/diff)
   │                 │
   └────► C3 registro+fatores (alimenta contexto de C1/C2) ──► C4 MCP+agente
```

1 marco = 1 PR, como sempre. C1 é o primeiro por ser o de maior valor/esforço
(solver, contexto, guarda-corpo e pipeline assíncrono já existem — o grosso é o
vocabulário de diretrizes §2.1 e as métricas §2.3). C3 vale antecipar no que
depender de dados: quanto antes o `RegistroExecucao` existir, mais histórico os
fatores terão quando C1/C2 o consumirem.
