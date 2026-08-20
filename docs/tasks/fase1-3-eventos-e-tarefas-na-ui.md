# Fase 1.3 — Evento × Tarefa: tornar a separação visível

> **Plano (passo 3 do ciclo). Gate respondido em 19/08/2026** — ver §5.
>
> Origem: dogfooding de **19/08/2026**. O usuário descreveu a separação "eventos
> (aula, reunião — fixos) × tarefas (estudar, trabalho — com prazo, passam pelo
> algoritmo)" **como se fosse uma proposta nova** — sendo que ela já é a arquitetura
> desde o MVP. É o sintoma: o modelo está certo e a interface não o conta.

**Cortada em duas tasks** (decisão do gate, Q5):

| | Entrega | Repos |
| --- | --- | --- |
| **1.3a** | criar evento pela UI, com recorrência, data limite e feriados; `data_fim` obrigatório; Topbar enxuta | **backend + frontend** |
| **1.3b** | vocabulário ("Tarefas") e distinção visual entre bloco planejado e fixo | frontend |

A 1.3a é a **primeira task de dois repos** sob a metodologia de dois agentes em
paralelo (`CLAUDE.md`, "Task que toca os DOIS repos"). O §6 deste documento é o
**contrato** — a única costura entre os dois lados.

## 1. O que a task é

Fazer a interface dizer o que o backend já faz, e devolver ao usuário o controle que o
modelo tem e a tela não expõe.

**Não é** mudar o modelo de `Evento`/`Tarefa`: eles já são o que precisam ser.
**Não é** mexer no solver.

## 2. Análise — medido em 19/08/2026

### 2.1 Não existe caminho para criar um evento na interface

`store/apiStore.jsx:211` tem `addEvento`, que faz `POST /eventos/` e recarrega a
janela. **Nenhum componente o chama.** O único formulário de criação é
`NovaTarefaForm.jsx`, alcançado pelo "+" do Inbox na `Sidebar`.

Um evento só nasce por **promoção de tarefa**, pelo **agente**, pela **API** ou pelo
**admin**. É a explicação de por que as 3 disciplinas com PDF viraram 58 avulsos.

### 2.2 A recorrência trafega inteira, e ninguém a preenche

`store/mappers.js:76-89` lê e escreve `ignorar_feriados` e `data_fim`; `types.js:69`
os documenta; `lib/recurrence.js` os respeita e tem teste para os dois. O backend os
expõe em `serializers.py:120`. **Pronto dos dois lados, sem um único controle na tela.**

### 2.3 `data_fim` aceita nulo — e há séries infinitas em produção

8 `RegraRecorrencia` no banco real, **2 sem `data_fim`**: `Academia (seg/sex)` e
`Academia (qua)`.

### 2.4 A Topbar já está cheia

Seis controles na área de ações: tema, Pendentes, Assistente, **Replanejar**, Editar,
Planejar — além da navegação inteira à esquerda. O `Replanejar` é o mais deslocado: é
ação **sobre o plano existente** (fica `disabled` durante o modo Planejar), não um modo
global irmão dos outros. Já existe `ReplanejarPanel.jsx`.

### 2.5 Nada na tela distingue bloco planejado de bloco fixo

`EventBlock.jsx`: *"a COR vem da classe; o ESTADO é tratamento (borda/opacidade/ícone),
nunca outra matiz"*. Borda e ícone já significam pendente/concluído. O dado para
diferenciar existe (`origem_tarefa` vem no payload) e não é usado. → **1.3b**.

## 3. Desenho — 1.3a

### 3.1 Backend: `data_fim` obrigatório

`RegraRecorrencia.data_fim` passa a `null=False`. Raio de impacto medido:

| Onde | O quê |
| --- | --- |
| `models.py` | `data_fim` deixa de aceitar nulo |
| migration `0013` | **backfill primeiro**: as 2 séries da academia recebem `2026-12-31`; só então `AlterField` |
| `seed_demo.py`, `seed_planejamento.py` | 4+ `RegraRecorrencia.objects.create(...)` passam a informar `data_fim` |
| `tests/factories.py` | `RegraRecorrenciaFactory` ganha default |
| `serializers.py` | fica obrigatório sozinho (DRF deriva de `null=False`); a mensagem de erro precisa ser em linguagem de usuário |

Ordem da migration importa: `AlterField` antes do backfill quebra com `NOT NULL`.

### 3.2 Frontend: criar evento

Dois caminhos (Q2):

- **clicar num horário vazio** do calendário → formulário de evento com data/hora já
  preenchidas;
- **botão "Novo"** na Topbar → escolha entre evento e tarefa, com uma linha explicando
  a diferença: *Evento — hora marcada, você define. Tarefa — tem prazo e esforço, o
  planejador escolhe quando.*

Formulário de evento: título, classe, data, hora início/fim, descrição, e o bloco
**Repetir**: dias da semana, **até (obrigatório)** e a caixa **"não repetir em
feriados"**.

### 3.3 Frontend: Topbar enxuta

`Replanejar` sai da Topbar e passa a viver dentro do fluxo **Planejar**. O **tema** vai
para um menu `...`. Resultado: `[+ Novo] [Pendentes] [Assistente] [Editar] [Planejar]`.

## 4. Testes

**Backend:** a migration backfilla as 2 séries e não quebra; criar regra sem `data_fim`
é 400 com mensagem legível; seeds continuam rodando; a suíte inteira verde.

**Frontend:** o formulário monta o payload certo (dias, `data_fim`, `ignorar_feriados`);
série sem data limite é barrada antes do POST; clicar em horário vazio abre o
formulário com data/hora corretas; `Replanejar` não está mais na Topbar.

**Integração** (depois dos dois): criar pela UI uma série semanal com data limite e
"não repetir em feriados", e conferir que ela aparece nas datas certas e **some no
feriado**.

## 5. Gate — respondido em 19/08/2026

- **Q1 — `data_fim` obrigatório NO BANCO**, não só no formulário. As 2 séries da
  academia recebem **31/12/2026** (a academia é planejada por ano; ano que vem é outra
  decisão consciente). Isso torna a 1.3a uma task de **dois repos**.
- **Q2 — os dois caminhos**: clique no horário vazio **e** botão "Novo" na Topbar.
- **Q3 — "Inbox" vira "Tarefas"**, com subtítulo dizendo que o planejador as encaixa na
  semana. → **1.3b**.
- **Q4 — distinção visual do bloco planejado**: adiada para a **1.3b**, com proposta
  desenhada. É decisão que só se toma vendo na tela.
- **Q5 — partir em 1.3a e 1.3b.**
- **Extra, levantado pelo usuário:** a Topbar está cheia. `Replanejar` entra no fluxo
  Planejar e o tema vira menu — entra na **1.3a**, porque é lá que o "Novo" disputa
  espaço.

## 6. CONTRATO — a costura entre os dois agentes

**Nada aqui pode mudar de um lado só.** Se precisar mudar, volta para este documento e
o outro lado é avisado.

### 6.1 `POST /api/v1/eventos/` — já existe, sem mudança de forma

```json
{
  "titulo": "Análise de Sistemas Lineares",
  "descricao": "",
  "inicio": "2026-08-24T15:50:00-03:00",
  "fim": "2026-08-24T17:30:00-03:00",
  "classe": "<uuid da classe>",
  "rastrear_conclusao": false,
  "regra_recorrencia": {
    "tipo": "SEMANAL",
    "dias": [0],
    "ignorar_feriados": true,
    "data_fim": "2026-12-17"
  }
}
```

- **`dias`**: `0=seg … 6=dom`. Lista não-vazia; para `SEMANAL`, cada item em `0..6`.
- **`inicio`/`fim`**: ISO-8601 **com offset**. O backend guarda UTC; mandar sem offset
  desloca o evento em 3 horas, em silêncio.
- **`regra_recorrencia`**: ausente ou `null` ⇒ evento avulso.
- **`data_fim`**: `AAAA-MM-DD`. **Passa a ser obrigatório** quando há
  `regra_recorrencia` — é a única mudança de contrato desta task.
- **`classe`**: UUID de uma classe **do próprio dono**. Id de outro perfil responde
  **400** com "inexistente" (nunca 403, nunca 404 — a resposta não distingue "não
  existe" de "não é seu").

### 6.2 Erros que o frontend precisa tratar

| Situação | Status | Corpo |
| --- | --- | --- |
| `data_fim` ausente com recorrência | **400** | `{"regra_recorrencia": {"data_fim": ["..."]}}` |
| `dias` vazio ou fora de 0–6 | **400** | `{"regra_recorrencia": {"dias": ["..."]}}` |
| `fim` <= `inicio` | **400** | `{"fim": ["..."]}` |
| classe de outro dono | **400** | `{"classe": ["... inexistente"]}` |

O frontend **valida `data_fim` antes de enviar** (mensagem imediata no formulário), mas
não confia só nisso: o 400 tem de ser exibido, porque o agente e a API também escrevem.

### 6.3 O que NÃO muda

`GET /eventos/`, o payload de leitura, `promover`, `planejar`, `concluir`, `remarcar` e
o formato das ocorrências seguem idênticos. A 1.3a não toca em nada disso.

## 7. Raio de impacto

- **Backend:** `models.py`, migration `0013`, 2 seeds, `factories.py`, `serializers.py`.
- **Frontend:** formulário novo, `Topbar`, `DayColumn` (clique no vazio), `Sidebar`
  (o "+" continua criando tarefa), e o fluxo Planejar recebendo o `Replanejar`.
- **Dado real:** as 2 séries da academia ganham `data_fim = 2026-12-31`. Backup antes.
