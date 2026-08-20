# Notas de design e contextos de task

Índice das notas em `docs/tasks/`. O ciclo de trabalho está em `CLAUDE.md`
("Convenção de trabalho — ciclo por task") e a ordem das tasks em `ROADMAP.md`.

Dois tipos de documento:

- **Plano** (`fase*.md`, `*-implementacao.md`, `visao-*.md`) — escrito **antes** de
  codar, com as dúvidas explícitas. É o que o usuário revisa (passo 4 do ciclo).
- **Contexto** (`contexto-*.md`) — escrito **depois**, fechando a task: o que foi
  feito, decisões, bugs e o estado em que a próxima começa. Existe porque o contexto
  do agente é zerado entre tasks.

---

> ⚠️ **Antes de tudo: [`HANDOFF.md`](HANDOFF.md)** — configuração local desta máquina, o
> Supabase já provisionado e as branches ainda não mergeadas no `main`.

## Onde estamos (19/08/2026)

**Onde paramos:** **Fase 1.2 / PR B** (o importador), fechado em 19/08/2026 com o
ciclo inteiro: implementação, 46 testes novos (suíte **487 → 533**, todos verdes),
verificação contra o banco real em transação com rollback, backup
(`backup-planejador-20260819-2138.sql`) e o [contexto](contexto-fase1-2-prb.md).

**Próxima task: Fase 1.2 / PR C** — transcrever os 3 PDFs de `../../aulas/` (ASL, EG1,
Redes) para JSON, rodar `importar_planejamento_ensino --aplicar --substituir` nas três
e replanejar do zero (decisão D6). O roteiro está no §3.6 do plano-pai e as três
ressalvas que o PR B levantou, no §7 do [contexto dele](contexto-fase1-2-prb.md).
**É a primeira task da 1.2 que muda o calendário real do usuário.**

**Duas frentes abertas, e a ordem entre elas é decisão do usuário** — está registrada
como decisão em aberto no `ROADMAP.md` ("Ordem: fechar a 1.2 ou voltar para a Fase 0"):

| Frente | Estado | O que falta para andar |
| --- | --- | --- |
| **Fase 1.2** (dogfooding) | PR A ✅, PR B ✅ | PR C (transcrição + migração). Tem **relógio**: o semestre roda até 17/12/2026 e o dado só aparece com ele vivo |
| **Fase 0B / PR2** (a espinha) | ⏸️ parada desde 08/08 | escrever o plano de implementação (passo 3). **Não está mais bloqueada** |

> ⚠️ **O PR2 deixou de estar bloqueado em 08/08/2026** e este arquivo continuou
> dizendo "Bloqueado" por onze dias — foi o que produziu a deriva. O pré-requisito
> externo (projeto Supabase criado, ES256 confirmado, URLs configuradas, assinatura
> verificada ponta a ponta) está **cumprido**, ver [`fase0b-pr2-supabase.md`](fase0b-pr2-supabase.md)
> e o §3 do [`HANDOFF.md`](HANDOFF.md). O que falta é trabalho nosso, não de terceiro.

## Fase 0B / PR2 — Supabase Auth (0B.1/0B.2)

O PR1 deixou este PR estreito de propósito: o backend já é multi-tenant ponta a ponta,
então o PR2 troca essencialmente **uma função** —
`services/perfis.perfil_do_request()`, que hoje devolve sempre o perfil local e tem no
próprio docstring a marcação de que é o ponto de troca. O roteiro está na seção 8 de
[`contexto-0b-pr1.md`](contexto-0b-pr1.md) e as consequências no backend
(`PyJWT[crypto]`, cache de JWKS com refetch por `kid` desconhecido, claims a validar,
credencial de serviço do MCP) no plano do Supabase.

Falta escrever o **plano de implementação** (passo 3 do ciclo) — é o único artefato
que separa o PR2 de começar.

Dois pontos que o plano tem de tratar, já achados:

1. **`email_verified` vem `true` sem verificação nenhuma** (confirmação de email
   desligada no projeto). O claim não é prova de identidade; a chave é o `sub`.
2. **O PR2 põe o uso pessoal do usuário atrás de um login que depende de internet**, e
   ele está usando o sistema de verdade. É trade-off conhecido, registrado nos riscos
   do plano do Supabase, e ainda não decidido.
3. **Metade do PR2 é frontend** (login via `supabase-js` + `Authorization: Bearer`), e o
   passo 10 do ciclo só fecha com o frontend verde. Não dá para planejá-lo como task
   só-de-backend — recuperado em 19/08/2026 de um commit que ficou fora do `main`, e
   agora registrado no item 0B.2 do `ROADMAP.md`.

Tasks de encaixe já concluídas enquanto isso: **0A.1** (`LLMProvider`) e **0A.3**
(instrumentação) — ver Concluídas abaixo.

## Fase 1.1 — Parâmetros de agendamento por tarefa

Plano [`fase1-parametros-por-tarefa.md`](fase1-parametros-por-tarefa.md) (gate
respondido em 15/08/2026, §7). Nasceu do dogfooding que achou estudo de prova agendado
**2 meses antes** da prova e mediu o teto do 7b local.

| PR | Estado |
| --- | --- |
| **A** — campos + `TARDE` no solver + limites duros/suaves | ✅ [contexto](contexto-fase1-pra.md) |
| **B** — `aplicar_plano` como ferramenta + `a_partir_de` no caminho que persiste | ✅ [contexto](contexto-fase1-prb.md) |
| **C** — IA lê `descricao` → knobs, `pergunta` no plano, vocabulário | ✅ [contexto](contexto-fase1-prc.md) — mecanismo pronto, mas **ocioso com o 7b local** (ver §4 de lá) |
| **C2** — `atualizar_tarefa` (agente não tinha como EDITAR: duplicava) | ✅ [contexto](contexto-fase1-prc2.md) |
| **D** — handoff da conversa forte (JSON único) | ⏸️ **travado nas decisões D6/D7**, que são de produto, não técnicas |

> **A task está entregue, mas o bug que a motivou só está corrigido em parte.** Medido
> no banco real em 19/08/2026: das **41 tarefas, 9 têm `estrategia=TARDE` e 32 seguem
> sem estratégia nenhuma** (`NULL`). Não há default por decisão do gate (D2), o
> frontend não mostra o campo e o 7b local não preenche — então quem não passou pelo
> `manage.py marcar_estrategia` continua sendo agendado o quanto antes. Fechar essa
> conta é trabalho de dado, não de código.

> **Arquitetura do beta — reconfirmada em 24/07/2026.** Cogitou-se pôr também os
> **dados** no Supabase; foi avaliado e recusado. Segue valendo: **Supabase só para
> Auth**, dados no Postgres local de cada testador, IA local. O trilema que fecha a
> questão está em "Arquitetura do beta" no `ROADMAP.md` — vale ler antes de propor
> mudança de infraestrutura.

## Fase 1.2 — Aula é bloco fixo; conteúdo, prova e entrega são da ocorrência

Plano [`fase1-aula-fixa-conteudo-por-ocorrencia.md`](fase1-aula-fixa-conteudo-por-ocorrencia.md)
(gate respondido em 18/08/2026, §7).

| PR | Estado |
| --- | --- |
| **A** — `titulo/descricao/classe_override` na `Ocorrencia` + resolução na leitura | ✅ [contexto](contexto-fase1-2-pra.md) |
| **B** — `importar_planejamento_ensino` (JSON por disciplina, idempotente) | ✅ [plano](fase1-2-prb-importador.md) · [contexto](contexto-fase1-2-prb.md) |
| **C** — transcrever os 3 PDFs, migrar os 67 avulsos, replanejar do zero | 🔜 **próxima** |
| **D** — editar conteúdo de ocorrência pela UI (escrita pela API) | ⏳ quando a UI pedir |

## Fase 1.3 — Evento × Tarefa: tornar a separação visível

Plano [`fase1-3-eventos-e-tarefas-na-ui.md`](fase1-3-eventos-e-tarefas-na-ui.md),
**gate respondido em 19/08/2026**. Cortada em **1.3a** (criar evento pela UI,
`data_fim` obrigatório, Topbar enxuta — **backend + frontend**) e **1.3b** (vocabulário
e distinção visual — frontend).

**A 1.3a estreia a metodologia de dois agentes em paralelo.** O §6 do plano é o
contrato entre os dois lados, e é o único ponto onde eles se encontram.

Nasceu de um sintoma incômodo do dogfooding de 19/08/2026: o usuário descreveu a
separação evento × tarefa **como proposta nova**, sendo que ela é a arquitetura desde o
MVP. O modelo está certo; a interface não o conta.

O buraco funcional que a análise achou: **não existe caminho para criar evento na UI**.
`store/apiStore.jsx:211` tem `addEvento` ligado à API e **nenhum componente o chama**.
`ignorar_feriados` e `data_fim` trafegam inteiros dos dois lados e não têm um controle
na tela. É a explicação de por que as 3 disciplinas com PDF viraram 58 avulsos.

Nasceu do mesmo dogfooding: as 3 disciplinas com PDF de planejamento de ensino foram
lançadas como **58 eventos avulsos + 9 provas**, em vez de série recorrente. A
`Ocorrencia` não tinha onde pendurar o conteúdo de uma data — ganha
`titulo_override`/`descricao_override`/`classe_override`, e a prova passa a chamar
atenção pela **cor da classe**, que é a regra que o frontend já tem. Fecha com um
comando de importação, porque os PDFs das outras 4 disciplinas ainda vêm.

**Os 58 + 9 continuam lá** — conferido no banco em 19/08/2026: classe `Aula` tem 62
eventos, dos quais **58 avulsos** e 4 recorrentes; classe `Prova` tem **9, todos
avulsos**. O PR A deu o lugar onde pendurar o conteúdo; quem move o dado para lá é o
PR C, e ele depende do B.

Duas coisas que o PR B tem de respeitar e são fáceis de esquecer (do §6 do contexto do
PR A):

1. **ASL não cabe em uma `RegraRecorrencia`** — seg 15:50–17:30 e qui 15:50–18:40:
   mesma hora, durações diferentes, e a regra guarda um horário só. São **dois
   eventos**, como a academia.
2. **Chame `full_clean()` ao gravar `Ocorrencia`** — é o que aciona a checagem de dono
   da `classe_override`.

## Concluídas

| Task | Contexto | Resumo |
| --- | --- | --- |
| **0A.3** — instrumentação das chamadas de IA | [`contexto-0a3-instrumentacao.md`](contexto-0a3-instrumentacao.md) · [plano](fase0a3-instrumentacao.md) | `services/telemetria.py`: um registro JSONL por chamada (duração, tokens, tok/s, carga separada), nas 4 famílias; nunca grava conteúdo; `LOGGING` passou a existir no settings |
| **0A.1** — abstração `LLMProvider` | [`contexto-0a1-llmprovider.md`](contexto-0a1-llmprovider.md) · [plano](fase0a1-llmprovider.md) | os 3 pontos com `ollama.Client` direto passaram a `services/llm.py` (Ollama/Anthropic/Mock por `LLM_PROVIDER`); sem mudança de contrato |
| **0B / PR1** — `Perfil`, `dono` e default invertido | [`contexto-0b-pr1.md`](contexto-0b-pr1.md) | `Perfil` + FK `dono` nos 8 models-raiz, unicidade por-dono e um manager que **recusa consulta sem escopo**; 41 testes novos de isolamento com dois perfis |
| **0B / PR0** — views finas + agente em processo | [`contexto-0b-pr0.md`](contexto-0b-pr0.md) | Regra saiu das views para `services/tarefas.py` e `services/agenda.py`; as ferramentas do agente deixaram de falar HTTP com a própria API |

## Planos e visões anteriores (Fases A e C, já implementadas)

| Documento | Sobre |
| --- | --- |
| [`visao-rotina-inteligente.md`](visao-rotina-inteligente.md) | Visão dos marcos C1–C8 (cenários, replanejar, agente, adaptação) |
| [`rotina-inteligente-implementacao.md`](rotina-inteligente-implementacao.md) | Plano de implementação dos marcos C1–C4 |
| [`planejamento-ia-analise.md`](planejamento-ia-analise.md) | Análise da camada de IA sobre o solver |
| [`planejamento-ia-implementacao.md`](planejamento-ia-implementacao.md) | Plano da Fase A (solver + diretrizes de IA) |
| [`planejamento-producao-multitarefa.md`](planejamento-producao-multitarefa.md) | Desenho do solver multitarefa |
