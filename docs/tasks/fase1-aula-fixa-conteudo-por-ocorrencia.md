# Fase 1.2 — Aula é bloco fixo; conteúdo, prova e entrega são da ocorrência

> **Status:** plano escrito em 18/08/2026, **aguardando o gate do passo 4** (§7).
> Segunda regra de negócio fechada por dogfooding real, depois da
> [1.1](fase1-parametros-por-tarefa.md).

## 1. O que a task é (e o que não é)

A regra que o uso fechou:

> **A aula é um compromisso fixo e recorrente. O que varia de semana para semana é o
> CONTEÚDO daquela data — inclusive "hoje tem prova" e "hoje entrega o trabalho". O
> ESFORÇO (estudar, escrever o relatório, programar) nunca é aula: é `Tarefa`, com
> deadline no instante em que a coisa acontece.**

O modelo já sabe fazer isso — `Evento` + `RegraRecorrencia` para o fixo, `Tarefa` com
`deadline`/`estrategia` para o esforço. O que faltou foi **onde pendurar o conteúdo de
uma data específica** de uma série recorrente. Sem esse lugar, o lançamento das três
disciplinas que tinham PDF de planejamento de ensino degenerou em **58 eventos avulsos,
um por semana**, e as **9 provas viraram eventos próprios** — a série recorrente sumiu,
e com ela a ideia de "aula fixa".

**É desta task:** dar à `Ocorrencia` overrides de conteúdo (`titulo`, `descricao`,
`classe`), fazê-los aparecer na leitura da agenda, e um comando de importação que
transforme um planejamento de ensino em série recorrente + ocorrências.

**Não é desta task:** editar conteúdo de ocorrência pelo frontend (§7 D3); ler PDF
automaticamente (§3.5); mexer no solver — ele não muda uma linha.

## 2. Estado atual (o que a análise achou)

### 2.1 O que está no banco (perfil local, semestre 2026/2)

| | Como está | Certo? |
| --- | --- | --- |
| Física Exp. 2, Física 4, Oficina 2, Sistemas Inteligentes | 4 eventos **recorrentes**, `ignorar_feriados=true`, `rastrear_conclusao=false` | ✅ |
| ASL, EG1, Redes | **58 eventos avulsos** (ASL 28, Redes 16, EG1 14), com o conteúdo no título | ❌ |
| Provas | **9 eventos avulsos** na classe `Prova`, ocupando o horário da aula | ❌ |
| Trabalhos, relatórios, listas, preparo de prática, estudo | **41 `Tarefa`s** com `deadline`, as de prova já com `estrategia=TARDE` | ✅ |
| `Ocorrencia` | **0 linhas** | — |

Ou seja: o lado do esforço já está no formato certo desde a 1.1. O que está errado é só
o lado do calendário, e só nas 3 disciplinas cujo PDF foi lançado à mão.

### 2.2 As outras 4 disciplinas vão chegar

Os PDFs de planejamento de ensino das outras 4 ainda vão sair. **Isso é premissa de
desenho, não detalhe:** se importar um planejamento continuar sendo trabalho manual de
lançar evento a evento, o erro se repete 4 vezes. O caminho tem de ser um comando.

### 2.3 A regra de cor do frontend já decide como a prova chama atenção

`components/EventBlock.jsx` abre com a regra, e ela é explícita:

> "Regra inviolável: a **COR** vem sempre da classe; o **ESTADO** é tratamento
> (borda/opacidade/ícone), nunca outra matiz."

Isso resolve "como a prova se destaca" sem inventar vocabulário visual: o dia de prova
sai na cor da classe `Prova` (`#fbeaea`, semeada em `services/perfis.py:25`). Borda,
opacidade e ícone estão **tomados** pelo status (pendente/concluído) — usá-los para
"tem prova" colidiria com a única sinalização que o calendário já tem.

### 2.4 Duas coisas que a leitura da agenda já faz certo

- `views.py:234 _payload_ocorrencia` **já** sobrescreve `inicio`/`fim`/`status` do
  evento pelo valor da ocorrência antes de responder. Acrescentar título, descrição e
  classe ali é o mesmo gesto, no mesmo lugar.
- `store/mappers.js:126-135` lê `item.titulo`, `item.descricao` e `item.classe?.id` do
  payload, sem saber se vieram da série ou da ocorrência. **Se o override for resolvido
  no servidor, o frontend não muda uma linha para exibir.**

### 2.5 Uma armadilha de recorrência: ASL não cabe em uma regra

`RegraRecorrencia` guarda **um** horário de início e a duração vem de `fim - inicio` do
evento — os mesmos para todos os `dias` da regra. ASL é seg 15:50–17:30 (100 min) e qui
15:50–18:40 (170 min): **mesma hora, durações diferentes**. Uma regra não expressa isso.

É a mesma limitação que já obrigou a academia a virar duas regras (seg/sex 18:30 e qua
17:00). Então ASL precisa de **dois eventos recorrentes** com o mesmo título. Vale
conferir disciplina a disciplina na importação — as outras 6 cabem em uma regra cada.

## 3. Desenho proposto

### 3.1 `Ocorrencia` ganha três overrides de conteúdo

Hoje ela tem `inicio_override`, `fim_override`, `status_override` (models.py:325). Somar:

```python
titulo_override = models.CharField(max_length=200, blank=True)
descricao_override = models.TextField(blank=True)
classe_override = models.ForeignKey(
    Classe, null=True, blank=True, on_delete=models.PROTECT,
    related_name="ocorrencias_override",
)
```

Semântica, deliberadamente igual à dos overrides que já existem: **preenchido
substitui, vazio herda a série**. `descricao_override` **substitui** e não concatena —
a `descricao` do evento fica para o que vale o semestre inteiro (professor, ementa,
critério de avaliação) e a da ocorrência para o que vale aquele dia.

`classe_override` é `PROTECT` como `Evento.classe`: apagar a classe `Prova` com dias de
prova pendurados nela tem de doer, não sumir em silêncio.

**Uso pretendido:**

| Dia | `titulo_override` | `descricao_override` | `classe_override` |
| --- | --- | --- | --- |
| Aula comum | — (nome fixo da disciplina) | conteúdo da semana | — |
| Dia de prova | `ASL — Prova Teórica 1` | assunto cobrado | `Prova` |
| Dia de entrega | `Redes — entrega do Prog3` | conteúdo + o que entrega | — (ver D2) |
| Semana sem aula | — | — | — (só `status_override=PULADO`) |

### 3.2 A consequência conceitual, dita na cara

O `CLAUDE.md` lista como invariante: *"Ocorrências de eventos recorrentes são virtuais:
só existe linha `Ocorrencia` quando o usuário toca aquela data"*. Isso **muda**: passa a
existir linha também para **carregar conteúdo**, sem o usuário ter tocado em nada.

É uma ampliação consciente, e o custo é medido: ~18 datas por disciplina × 7
disciplinas ≈ **120 linhas no semestre**. A expansão continua sob demanda (nada
materializa série), o `unique (evento, data)` continua sendo a guarda, e
`completion._get_or_create_ocorrencia` continua funcionando — concluir um dia que já
tem conteúdo só preenche o `status_override` da linha que já está lá.

O que **não** muda: `Ocorrencia` segue sem `dono`, herdando pelo evento.

O invariante tem de ser reescrito no `models.py` e no `CLAUDE.md` na mesma task (passo
8). Invariante desatualizado é pior que invariante ausente.

### 3.3 Leitura: dois pontos, três linhas cada

1. `services/recurrence.py` — `OcorrenciaView` ganha `titulo`, `descricao`, `classe`, e
   `montar_ocorrencia` os resolve junto com os overrides que já resolve.
2. `views.py:234 _payload_ocorrencia` — sobrescreve `titulo`, `descricao` e `classe`
   (serializada por `ClasseSerializer`, como o resto do payload) quando houver.

O `agente.py` monta o próprio resumo da agenda (linhas 311 e 401, `ev.classe.nome`) a
partir do **evento**, não da view — então o agente continuaria dizendo "Aula" num dia de
prova. Corrigir junto: o resumo do agente lê a ocorrência quando ela existe.

### 3.4 O solver não muda

`planejamento.py` trata evento como tempo ocupado; a classe do bloco não entra na
conta, e nenhuma lógica no repo liga em `classe.nome` (auditado: só o agente, e só para
narrar). `PULADO` já libera o horário, que é o comportamento certo para semana sem aula.

### 3.5 O importador — `manage.py importar_planejamento_ensino`

Ao lado de `seed_demo`/`seed_planejamento`, e **não** um seed: escreve dado real.

- **Entrada: um JSON por disciplina**, versionado em `planner/fixtures/planejamento/`.
  Nada de parsear PDF: os 7 PDFs são heterogêneos, o parser seria mais frágil e mais
  caro que transcrever, e transcrever é uma vez por disciplina por semestre.
- **Forma:** cabeçalho da disciplina (código, nome, classe, `rastrear_conclusao`,
  período, uma ou mais recorrências com dia/hora/duração) + lista de datas, cada uma com
  `conteudo`, e opcionalmente `prova`, `entrega` ou `sem_aula`.
- **Idempotente por (disciplina, semestre)**, ao contrário dos seeds: rodar duas vezes
  não duplica, atualiza. É o que permite corrigir uma linha do JSON e re-rodar.
- `--substituir` apaga os eventos avulsos daquela disciplina antes de criar a série —
  é o caminho da migração dos dados atuais (§3.6).
- **Não cria `Tarefa`.** O JSON pode listar as avaliações, mas o esforço já está nas 41
  tarefas; misturar as duas escritas num comando só faria dele o lugar onde a próxima
  duplicata nasce. Conferência de tarefa faltante é relatório do comando, não escrita.

### 3.6 Migração dos dados que já estão lá

Não é migration de schema — é dado do usuário, num banco só. Roteiro:

1. `pg_dump` novo antes de qualquer coisa (o último é de 14/08).
2. Transcrever os 3 PDFs de `aulas/` para JSON, conferindo contra os 67 eventos atuais —
   eles são a transcrição anterior e servem de gabarito.
3. `importar_planejamento_ensino --substituir` nas 3 disciplinas: apaga 58 aulas avulsas
   + 9 provas, cria 4 séries (ASL são 2, §2.5) + ~58 ocorrências.
4. Conferir: nenhum evento de classe `Prova` avulso sobra; as 41 tarefas ficam intactas;
   os 46 blocos de estudo já promovidos continuam apontando para as tarefas de origem.

As outras 4 disciplinas já são séries corretas: quando o PDF chegar, o comando **só
acrescenta ocorrências**, sem `--substituir` e sem recriar evento.

## 4. Corte em PRs

| PR | Entrega | Depende de |
| --- | --- | --- |
| **A** | Model + migration `0011` + resolução na leitura (`recurrence`, `views`, `agente`) + admin + testes | — |
| **B** | `importar_planejamento_ensino` + schema do JSON + testes com fixture | A |
| **C** | Transcrição dos 3 PDFs + execução da migração de dados (§3.6) | B |
| **D** | Frontend: editar conteúdo de ocorrência pela UI | gate D3 |

O PR A **não muda contrato de leitura de forma quebrável**: campos que já existiam
passam a vir com outro valor em algumas ocorrências. O frontend não precisa acompanhar
para não quebrar (passo 9 vira "eis por que nada quebrou, e eis o que você ganha de
graça").

## 5. Testes

- `montar_ocorrencia` aplica cada override e herda o que estiver vazio.
- Ocorrência com `classe_override` sai no payload com a classe sobrescrita, e as **outras
  datas da mesma série não**.
- `PULADO` continua omitindo a data mesmo com conteúdo preenchido.
- Concluir uma ocorrência que já tem conteúdo **não** apaga o conteúdo
  (`_get_or_create_ocorrencia` + `update_fields`).
- `classe_override` de outro dono é recusada (isolamento — `test_isolamento.py`).
- Solver: dia com `classe_override=Prova` continua contando como ocupado igual a antes.
- Importador: idempotência (2 execuções = mesmo estado), `--substituir` apaga só a
  disciplina alvo, disciplina com 2 recorrências (ASL) cai no evento certo por dia da
  semana, data fora do período recusada.

## 6. Raio de impacto

| Arquivo | O quê |
| --- | --- |
| `planner/models.py` | 3 campos em `Ocorrencia`; reescrever o docstring do invariante |
| `planner/migrations/0011_*.py` | aditiva, sem default problemático |
| `planner/services/recurrence.py` | `OcorrenciaView` + `montar_ocorrencia` |
| `planner/views.py` | `_payload_ocorrencia` |
| `planner/services/agente.py` | resumo da agenda lê a ocorrência (linhas 311, 401) |
| `planner/admin.py` | colunas/filtro de `OcorrenciaAdmin` |
| `planner/management/commands/importar_planejamento_ensino.py` | novo |
| `planner/fixtures/planejamento/*.json` | novo, 1 por disciplina |
| `CLAUDE.md`, `ROADMAP.md`, `docs/tasks/README.md` | invariante + status da 1.2 |

## 7. Gate — dúvidas para o passo 4

**D1 — Título por ocorrência: sempre ou só em dia especial?**
Proposta: **só em dia especial**. Semana comum mantém o nome fixo da disciplina no bloco
(é o que faz "aula fixa" parecer fixa) e o conteúdo aparece ao abrir; prova e entrega
sobrescrevem o título, porque são o que a pessoa precisa ver de relance. Alternativa: o
título sempre traz o conteúdo — vira o calendário de hoje, mais informativo e mais
ruidoso.

**D2 — Entrega de trabalho muda a cor?**
Proposta: **não.** Se prova e entrega tiverem a mesma cor, "vermelho" deixa de querer
dizer prova. Entrega fica no título/descrição. Se você quiser destaque, o caminho certo
é uma classe `Entrega` com cor própria — decisão sua, e é barata (uma linha em
`perfis.py`, mas afeta todo perfil novo).

**D3 — Escrita pela API/frontend entra agora?**
Proposta: **não no PR A.** Hoje ninguém escreve `Ocorrencia` a não ser
`concluir`/`remarcar`; o conteúdo entra pelo importador e pelo admin. Um
`PATCH /eventos/{id}/ocorrencias/{data}/` só se justifica quando a UI for editar isso —
vira o PR D, com o frontend junto. Se você quiser editar conteúdo à mão já no dogfooding,
diga: aí ele sobe para o PR A.

**D4 — Provas de recuperação, que são condicionais.**
Duas ("só se ficar < 6,0", 15/12 e 17/12) podem não acontecer. Marco a ocorrência como
`Prova` desde já (e você "pula" o dia se não precisar), ou deixo sem marcação até saber?
Proposta: **marcar** — a prova condicional que você esquece é a que dói.

**D5 — O JSON transcrito é fonte da verdade ou insumo descartável?**
Proposta: **fonte da verdade**, versionada no repo. Semestre que vem o arquivo é
editado, não redigitado, e o diff mostra o que a coordenação mudou no meio do semestre.
O custo é que dado de uso pessoal passa a morar num repo que um dia vira produto — hoje
não incomoda, mas é escolha, não acidente.

**D6 — Os 46 blocos de "Estudar ..." já promovidos.**
A migração de dados não os toca, então continuam onde o solver os pôs. Você quer
replanejar do zero depois da importação (e perder eventual ajuste manual que já tenha
feito), ou manter?

**D7 — Confirmação factual antes do PR C:** o horário de ASL é seg 15:50–17:30 **e**
qui 15:50–18:40? É o único caso que força dois eventos, e quero errar isso agora e não depois de apagar 67 linhas.
