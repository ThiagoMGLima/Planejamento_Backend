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

## Task ativa

**Fase 0B / PR2 — Supabase Auth (0B.1/0B.2).**
🚧 **Bloqueado: exige o projeto Supabase criado** — isso é do usuário.

O PR1 deixou este PR estreito de propósito: o backend já é multi-tenant ponta a
ponta, então o PR2 troca essencialmente *uma função*
(`services/perfis.perfil_do_request`). O roteiro está na seção 8 de
[`contexto-0b-pr1.md`](contexto-0b-pr1.md).

## Concluídas

| Task | Contexto | Resumo |
| --- | --- | --- |
| **0B / PR1** — `Perfil`, `dono` e default invertido | [`contexto-0b-pr1.md`](contexto-0b-pr1.md) | `Perfil` + FK `dono` nos 8 models-raiz, unicidade por-dono e um manager que **recusa consulta sem escopo**; 39 testes novos de isolamento com dois perfis |
| **0B / PR0** — views finas + agente em processo | [`contexto-0b-pr0.md`](contexto-0b-pr0.md) | Regra saiu das views para `services/tarefas.py` e `services/agenda.py`; as ferramentas do agente deixaram de falar HTTP com a própria API |

## Planos e visões anteriores (Fases A e C, já implementadas)

| Documento | Sobre |
| --- | --- |
| [`visao-rotina-inteligente.md`](visao-rotina-inteligente.md) | Visão dos marcos C1–C8 (cenários, replanejar, agente, adaptação) |
| [`rotina-inteligente-implementacao.md`](rotina-inteligente-implementacao.md) | Plano de implementação dos marcos C1–C4 |
| [`planejamento-ia-analise.md`](planejamento-ia-analise.md) | Análise da camada de IA sobre o solver |
| [`planejamento-ia-implementacao.md`](planejamento-ia-implementacao.md) | Plano da Fase A (solver + diretrizes de IA) |
| [`planejamento-producao-multitarefa.md`](planejamento-producao-multitarefa.md) | Desenho do solver multitarefa |
