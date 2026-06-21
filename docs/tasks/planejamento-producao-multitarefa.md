# Planejamento de produção multitarefa (cálculo no backend)

Especificação para o time de backend. Substitui o cálculo de divisão que hoje é
feito **no frontend** (`lib/planner.js` + `POST /tarefas/{id}/planejar/`, uma
tarefa por vez) por um **planejador multitarefa calculado no servidor**.

## 1. Objetivo

O usuário abre um modal, **seleciona várias tarefas (To Dos)** do Inbox e o
backend calcula **um único planejamento que considera todas as tarefas juntas** —
distribuindo o tempo de produção de cada uma em sessões (eventos) antes da
respectiva deadline, **sem conflitar** com eventos já existentes nem entre si.

Cada To Do elegível tem:
- `esforco_estimado` (minutos de produção) — soma das sessões deve dar isso.
- `deadline` (data/hora limite) — as sessões da tarefa terminam antes dela.
- `classe` — define a cor dos eventos criados.

## 2. Fluxo (2 passos: calcular → aplicar)

Mantém o padrão "app sugere, usuário ajusta":

1. **Calcular (preview, NÃO persiste):** frontend manda os `tarefa_ids`
   selecionados + preferências. Backend devolve o plano proposto (sessões por
   tarefa) e o que não coube.
2. **Usuário revisa/ajusta** as sessões no modal (dia/hora/duração, add/remove).
3. **Aplicar (persiste):** frontend manda a lista final de sessões; backend cria
   os eventos atomicamente e marca as tarefas como `PROMOVIDA`.

> Decisão sugerida: 2 endpoints separados (preview sem efeito colateral + apply).
> Alternativa: 1 endpoint com flag `commit=false|true`.

## 3. Endpoints

Seguir a convenção de barra de vocês (ações com `/` no fim).

### 3.1 `POST /api/v1/planejamento/calcular/` — preview (não persiste)

**Request**
```json
{
  "tarefa_ids": ["uuid-a", "uuid-b", "uuid-c"],
  "a_partir_de": "2026-06-22T08:00:00-03:00",   // opcional; default = agora
  "preferencias": {                              // opcional; todas com default
    "janela_inicio": "08:00",
    "janela_fim": "22:00",
    "evitar_fds": true,
    "max_min_por_dia_por_tarefa": 120,
    "max_min_por_dia_total": null,               // null = sem teto total
    "sessao_min": 30,
    "sessao_max": 120,
    "granularidade_min": 15
  }
}
```

**Response 200**
```json
{
  "sessoes": [
    {
      "tarefa_id": "uuid-a",
      "tarefa_titulo": "Estudar P2 Cálculo",
      "classe_id": "uuid-classe",
      "inicio": "2026-06-22T19:00:00-03:00",
      "fim": "2026-06-22T21:00:00-03:00",
      "dur_min": 120
    }
  ],
  "nao_alocado": [
    {
      "tarefa_id": "uuid-c",
      "tarefa_titulo": "Trabalho ASL",
      "minutos_restantes": 90,
      "motivo": "sem espaço livre antes da deadline"
    }
  ],
  "preferencias_usadas": { "...": "ecoa os defaults aplicados" }
}
```

- `sessoes`: todas as sessões propostas (de todas as tarefas), já sem conflito.
- `nao_alocado`: por tarefa, o que não coube nem após relaxar as preferências.
- `preferencias_usadas`: o que efetivamente valeu (para o front exibir).

### 3.2 `POST /api/v1/planejamento/aplicar/` — commit (cria os eventos)

Recebe a lista **revisada** pelo usuário. Generaliza o atual
`/tarefas/{id}/planejar/` para várias tarefas.

**Request**
```json
{
  "sessoes": [
    { "tarefa_id": "uuid-a", "inicio": "2026-06-22T19:00:00-03:00", "fim": "2026-06-22T21:00:00-03:00" },
    { "tarefa_id": "uuid-b", "inicio": "2026-06-23T19:00:00-03:00", "fim": "2026-06-23T20:30:00-03:00" }
  ]
}
```

**Comportamento (atômico, em `transaction.atomic`)**
- Para cada sessão cria um `Evento`: `titulo`/`descricao`/`classe` vêm da tarefa;
  `inicio`/`fim` da sessão; `rastrear_conclusao = True`; `status = AGENDADO`;
  `origem_tarefa = tarefa`.
- Marca cada tarefa envolvida como `Tarefa.Status.PROMOVIDA`.
- Retorna **201** com a lista de eventos criados (`EventoSerializer(many=True)`).

> Reaproveita exatamente a lógica de criação do `planejar` atual, só que agrupando
> as sessões por `tarefa_id`.

## 4. Algoritmo de cálculo (guloso EDF + anti-conflito + relaxamento)

Greedy "Earliest Deadline First" com prevenção de conflito e relaxamento de
preferências suaves. Simples, determinístico e bom o suficiente (não precisa de
solver). **Não garante ótimo**, mas garante: sem sobreposição e respeitando
deadlines quando há espaço.

### Entradas
- `agora` = `a_partir_de` (default: now), arredondado para cima na granularidade
  ou para o início da janela do dia.
- `tarefas` selecionadas: `{id, titulo, esforco_estimado, deadline, classe_id}`.
- `ocupado`: eventos existentes que cruzam o horizonte → intervalos `[inicio,fim]`
  tratados como **bloqueados** (busca em `Evento` na janela `[agora, max(deadline)]`).
- `prefs` (todas suaves).

### Passos
1. **Horizonte:** de `agora` até `max(deadline)` das tarefas selecionadas.
2. **Slots livres:** para cada dia do horizonte, pegue a janela
   `[janela_inicio, janela_fim]`, subtraia os intervalos `ocupado` e (se
   `evitar_fds`, na 1ª passada) pule sáb/dom. Resulta numa lista de intervalos
   livres, snap na `granularidade_min`.
3. **Ordene as tarefas por deadline ascendente** (EDF); desempate por menor folga
   (`deadline - agora - esforco`) e, depois, maior esforço.
4. **Para cada tarefa (em EDF):**
   - `restante = esforco_estimado`.
   - Percorra os slots livres **em ordem cronológica que terminem ≤ deadline**:
     - `dur = clamp(restante, sessao_min, sessao_max, tamanho_do_slot,
       teto_dia_restante_da_tarefa, teto_dia_total_restante)`.
     - Se `dur >= sessao_min` (ou for o resto final), **coloque** a sessão no
       início do slot (ou no horário preferido), **marque o intervalo como
       ocupado** (assim outras tarefas/sessões não reaproveitam), `restante -= dur`.
     - Atualize os contadores do dia (por tarefa e total).
     - Pare quando `restante == 0`.
   - **Se sobrou `restante`**, aplique relaxamentos NESTA ORDEM e re-tente só o
     que falta:
     1. permitir fins de semana;
     2. elevar/remover `max_min_por_dia_*`;
     3. estender a janela para o dia inteiro;
     4. permitir sessões menores que `sessao_min`.
   - Se ainda sobrar, registre em `nao_alocado` (`minutos_restantes`, `motivo`).
5. **Saída:** todas as sessões colocadas + `nao_alocado` + `preferencias_usadas`.

**Conflito:** como cada sessão colocada vira "ocupado" na mesma estrutura, as
próximas alocações (de qualquer tarefa) já a evitam. Cobre conflito com eventos
existentes E entre sessões novas.

## 5. Preferências (defaults)

| Campo | Default | Significado (suave) |
|---|---|---|
| `janela_inicio` / `janela_fim` | `08:00` / `22:00` | faixa horária preferida das sessões |
| `evitar_fds` | `true` | evitar sáb/dom |
| `max_min_por_dia_por_tarefa` | `120` | não grindar uma tarefa o dia todo |
| `max_min_por_dia_total` | `null` | teto de produção/dia somando tudo (null = sem teto) |
| `sessao_min` / `sessao_max` | `30` / `120` | evita fragmentos minúsculos / blocos longos demais |
| `granularidade_min` | `15` | snap dos horários |

Todas **suaves**: o algoritmo relaxa (na ordem do passo 4) antes de desistir.

## 6. Validações e casos de borda

- Tarefa selecionada **sem `deadline` ou sem `esforco_estimado`** → `422` com a
  lista das tarefas inválidas (o front já filtra, mas valide no servidor).
- `tarefa_ids` vazio → `400`.
- Tarefa já `PROMOVIDA`/inexistente → `404`/`400` (decidir: ignorar com aviso ou erro).
- `deadline` no passado (≤ `agora`) → tudo vira `nao_alocado` (motivo "deadline no passado").
- Fuso: usar datetimes tz-aware (como o resto da API). O front manda ISO com offset/Z.
- `aplicar`: validar `fim > inicio` por sessão e `tarefa_id` existente (reusar as
  validações do `PlanejarSessaoSerializer` atual).

## 7. Modelo de dados

**Sem migração de schema.** `Evento.origem_tarefa` já existe (FK → Tarefa). Uma
tarefa com várias sessões = vários `Evento` apontando para ela. O `calcular` não
persiste nada; o `aplicar` cria os eventos.

## 8. O que muda no frontend (resumo, para alinhar contrato)

- Sai o cálculo local (`lib/planner.js`, `suggestSessions`) — vira chamada ao
  `calcular`. O `POST /tarefas/{id}/planejar/` atual pode ser **aposentado** em
  favor do `aplicar` multitarefa (ou mantido para o caso 1-tarefa).
- Entrada: botão **"Planejar produção"** (global, ex.: cabeçalho do Inbox) abre o
  modal.
- Modal passo 1: lista de To Dos elegíveis (deadline + esforço, status INBOX) com
  **checkboxes** + controles de preferências (opcionais) + botão **Calcular**.
- Modal passo 2: plano agrupado por tarefa, sessões **editáveis** (reusa as linhas
  dia/hora/duração já existentes) + avisos de `nao_alocado` + botão **Aplicar**.
- `src/lib/api.js`: `planejamento.calcular(body)` e `planejamento.aplicar(body)`.

## 9. Decisões em aberto (confirmar com o produto)

1. **2 endpoints (calcular/aplicar)** vs 1 com `commit` — recomendo 2.
2. **`max_min_por_dia`**: por tarefa (recomendado) e/ou teto total/dia.
3. Considerar **eventos existentes como ocupados** — recomendo **sim** (evita
   choque com a rotina já agendada). Confirmar se inclui eventos recorrentes
   expandidos no horizonte.
4. Tarefa inválida na seleção: **ignorar com aviso** vs **erro 422** — recomendo
   422 no `calcular` (e o front pré-filtra).
