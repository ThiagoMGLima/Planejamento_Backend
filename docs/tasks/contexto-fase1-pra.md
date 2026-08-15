# Contexto — Fase 1.1 / PR A: estratégia e limites por tarefa

> **Documento de contexto (passo 7 do ciclo).** Escrito depois de implementar.
> Plano: [`fase1-parametros-por-tarefa.md`](fase1-parametros-por-tarefa.md).
> Data: 15/08/2026. Branch: `claude/fase1-pra-parametros-tarefa`.

## 1. O que era para fazer

Primeiro dos 4 PRs do plano: dar à `Tarefa` os parâmetros de agendamento e
ensinar o solver a agendar **colado no prazo**. Sem IA nenhuma — o PR A tem de
funcionar com `IA_PLANEJAMENTO_ENABLED=0`.

Motivo: o solver só sabia alocar o mais cedo possível, e agendava estudo de uma
prova de 20/10 para 17/08.

## 2. O que foi feito

| Arquivo | Mudança |
| --- | --- |
| `planner/models.py` | 6 campos em `Tarefa`: `estrategia` (choices CEDO/TARDE, **nulo por default**), `nao_antes_de`, `nao_depois_de`, `janela_inicio`, `janela_fim`, `dias_permitidos` |
| `migrations/0010_*.py` | os 6 campos, todos nulos ⇒ sem backfill automático |
| `services/planejamento.py` | `TarefaEntrada` +6 knobs; `TARDE`; `_snap_abaixo`; `_restricao_da_tarefa`; `_limites_duros`; `slots_livres(restricao=)`; `_alocar` bidirecional; `_deadline_efetiva` desacoplada; `montar_plano` lê os campos |
| `services/replanejamento.py` | o `SimpleNamespace` do pool passou a carregar os 6 campos |
| `serializers.py` | campos no `TarefaSerializer` + `validate()` de coerência |
| `management/commands/marcar_estrategia.py` | **novo** — marca estratégia em tarefas existentes; lista sem `--aplicar` |
| `tests/test_planejamento_parametros.py` | **novo** — 42 testes |

**Suíte: 296 → 338 testes, todos verdes.** `ruff`, `black --check` e
`makemigrations --check` limpos.

## 3. Decisões de desenho (e por quê)

### 3.1 TARDE é uma inversão de varredura, não um algoritmo novo

O "mais cedo possível" estava em dois pontos: `slots_livres` devolve slots em
ordem cronológica e `_alocar` pega os primeiros que couberem, ancorando a sessão
no **início** do slot. `TARDE` percorre `reversed(slots)` e ancora no **fim**.

O resto do solver não precisou saber da diferença:

- os tetos diários são dicionários por data ⇒ independem da ordem;
- a cascata de relaxamento re-roda e aloca o resto ⇒ TARDE recua **só** quando
  não coube, que é a semântica desejada, de graça;
- um slot nunca cruza a meia-noite (`slots_livres` monta dia a dia) ⇒ a data
  usada nos tetos é a mesma nas duas direções.

Foi preciso um `_snap_abaixo` (espelho do `_snap_acima`): na direção TARDE quem
vira horário de sessão é o **fim** do slot, e sem snap uma deadline às 15h07
produziria sessão fora do grid de 15 min.

### 3.2 Duro × suave, declarado por knob

O princípio de ortogonalidade (D1) obrigou a responder, por parâmetro, se a
cascata de relaxamento pode afrouxá-lo:

- **Duros** — `nao_antes_de` / `nao_depois_de`. Entram uma vez, em
  `_limites_duros`, **fora** do laço de níveis. Relaxar "não antes de X" seria
  desfazer o pedido, não degradá-lo. Não coube ⇒ `nao_alocado`.
- **Suaves** — janela e dias da tarefa. Compõem com as globais pelo **mais
  restritivo** (nunca ampliam) e caem no nível ≥ 3, junto com os overrides de
  janela que já existiam. Segurá-los no nível 3 (que abre o dia inteiro) geraria
  `nao_alocado` por preferência, não por falta de espaço.

Janela dura vazia (`nao_antes_de` > `nao_depois_de`, ou sem interseção com a
deadline) tem **motivo próprio** em `nao_alocado`, distinto de "sem espaço
livre": a causa é o pedido, não a agenda.

### 3.3 Acesso direto aos campos, sem `getattr` defensivo

`montar_plano` lê `t.estrategia` e companhia diretamente. Um `getattr(t, ..., None)`
deixaria qualquer objeto-tarefa incompleto **perder o parâmetro em silêncio** —
o modo de falha que este projeto evita por princípio. Quebrar alto é melhor.

Isso expôs um caso real: `replanejamento._pool_e_substituiveis` monta
`SimpleNamespace` com 5 campos. Sem estendê-lo, uma tarefa TARDE voltaria a ser
agendada o quanto antes **justamente no replanejamento** — e agora quebraria alto
em vez de degradar em silêncio. Corrigido e coberto por teste.

### 3.4 O comando de marcação

Existe porque a D2 tirou o default de classe: sem ele, as tarefas anteriores ao
PR ficariam todas sem estratégia e o bug seguiria vivo no calendário real.

Três travas para não virar um default disfarçado: **exige filtro** (`--classe`
e/ou `--titulo-contem`; recusa rodar em "todas"), **só lista sem `--aplicar`**, e
**pula quem já tem estratégia** salvo `--sobrescrever`.

## 4. Bugs e surpresas

- **Colisão de classe nos testes.** A fixture `perfil` já semeia as 5 classes
  padrão; criar `ClasseFactory(nome="Estudar")` batia na unicidade por-dono. Os
  testes do comando passaram a **buscar** a classe semeada.
- **Nenhuma regressão.** Os 296 testes existentes passaram sem alteração — o que
  era o objetivo dos defaults nulos.

## 5. Verificação no dado real

Tarefa de 180 min com deadline na PP1 de EG1 (20/10/2026 13:00):

```
antes (CEDO, comportamento único):     17/08 08:00–10:00 · 18/08 12:00–13:00
depois (estrategia=TARDE):             16/10 21:30–22:00 · 19/10 20:00–22:00
                                       20/10 12:30–13:00  ← acaba quando a prova começa
```

## 6. Estado em que a próxima task começa

**Pronto e no ar:** `TARDE`, limites duros, restrições suaves, contrato de API,
comando de marcação.

**Não feito de propósito (é dos próximos PRs):**

- Nada **liga** os campos automaticamente. O front não os mostra e a IA não os
  emite: hoje quem seta é a API, o admin ou o comando. *(PR C fecha isso.)*
- `criar_tarefa` (ferramenta do agente) **ainda não aceita** os knobs novos —
  entra no PR C, senão a IA cria tarefa que nunca terá estratégia.
- `aplicar_plano` como ferramenta do agente e `a_partir_de` no caminho que
  persiste continuam ausentes — é o **PR B**.
- D6 (privacidade da conversa remota) e D7 (onde vive o JSON único) seguem
  **abertas**; bloqueiam só o PR D.

**Pendência operacional:** rodar `marcar_estrategia --titulo-contem "Estudar para"
--estrategia TARDE --aplicar` no banco real. A simulação lista **10 tarefas**.
Não foi aplicado: é dado do usuário, e a decisão é dele.

## 7. Passo 8 — sincronia com o frontend

O contrato mudou, mas **só por adição**: `TarefaSerializer` passou a devolver 6
campos novos (decisão D4 — "oculto" é o front não mapear, não a API esconder).

Para o agente do frontend (repo vizinho `../../Frontend/Planejamento_Frontend/`):

> O `GET/POST/PATCH /api/v1/tarefas/` passou a incluir `estrategia`
> (`"CEDO"|"TARDE"|null`), `nao_antes_de`, `nao_depois_de` (datas ISO ou null),
> `janela_inicio`, `janela_fim` (`"HH:MM:SS"` ou null) e `dias_permitidos`
> (lista de 0–6 ou null).
>
> **Nada a fazer, por desenho:** são parâmetros que o usuário não digita. Não
> adicione ao `mappers.js` nem a nenhum formulário. Só confirme que o mapper
> ignora campos desconhecidos sem quebrar, e rode os E2E.
>
> Novos 400 possíveis ao escrever tarefa (nenhum atinge os campos que o front já
> manda): janela sem par ou invertida, `dias_permitidos` vazio ou fora de 0–6,
> `nao_depois_de` anterior a `nao_antes_de`.
