# Contexto — Fase 1.2 / PR B: `importar_planejamento_ensino`

*Fechado em 19/08/2026. Plano: [`fase1-2-prb-importador.md`](fase1-2-prb-importador.md)
(gate respondido no §6). Estado de onde partiu:
[`contexto-fase1-2-pra.md`](contexto-fase1-2-pra.md).*

## 1. O que era para fazer

Um comando que lê um JSON por disciplina e produz a **série recorrente** da aula mais
uma **`Ocorrencia` por data com conteúdo**. O PR A criou o *lugar* onde o conteúdo de
uma data mora; sem este comando, o único jeito de escrever lá é o admin, à mão — e foi
exatamente "à mão" que produziu os 58 eventos avulsos + 9 provas que a 1.2 existe para
consertar. Os PDFs das outras 4 disciplinas ainda vêm.

## 2. O que foi feito

| Onde | O quê |
| --- | --- |
| `models.py` | `Evento.chave_importacao` (CharField, blank) + `UniqueConstraint` **parcial** por `(dono, chave_importacao)` com `condition=~Q(chave_importacao="")` |
| `migrations/0012_chave_importacao.py` | aditiva: uma coluna e a constraint |
| `management/commands/importar_planejamento_ensino.py` | o comando (~430 linhas com a validação e o relatório) |
| `services/recurrence.py` | `datas_da_regra()` novo + `_rrule_do_evento()` extraído; `expandir` passa a usar o mesmo construtor de rrule |
| `fixtures/planejamento/README.md` | o schema do JSON documentado, para o PR C preencher |
| `tests/test_importar_planejamento.py` | 42 testes novos |
| `tests/test_recurrence.py` | 4 testes do contrato de `datas_da_regra` |

**Contrato externo:** inalterado. Nenhum endpoint, campo ou comportamento de API muda —
o passo 9 é "nada a fazer no frontend", pelo mesmo motivo do PR A.

## 3. As respostas do gate, e o que elas viraram

**Q1 — `chave_importacao`, e não casar por título.** O `Evento` não tinha onde registrar
"vim da importação de ASL 2026/2", e ASL tem **dois** eventos, então o título sozinho
não distinguiria. Sem chave, renomear a disciplina pela UI faria a próxima importação
criar uma **segunda série** — duas aulas no mesmo horário, em silêncio. A constraint é
**parcial** porque o vazio é o caso comum (todo evento que não veio de importação) e não
pode colidir consigo mesmo.

**Q2 — a aula marcada em feriado acontece.** Esta resposta me levou a um desenho
**errado**, corrigido no mesmo dia depois que o usuário viu o resultado no calendário.

*O que eu fiz primeiro, e por que estava errado:* forcei `ignorar_feriados=False` em
toda série importada e, para não sobrar aula fantasma no recesso, marquei `PULADO` em
**toda data que a regra gera e o JSON não lista**. Isso transferiu para o arquivo uma
decisão que é **da série**, e produziu um efeito ruim no calendário real: duas sextas
sumiram do mesmo jeito, sendo que uma era recesso declarado e a outra era só uma data
que eu não tinha transcrito.

*O modelo correto, que já existia:* a **regra de recorrência** diz quando acontece,
`ignorar_feriados` é escolha **por série**, `data_fim` limita. O JSON só acrescenta o
**conteúdo** de cada data. Como ficou:

| | Comportamento |
| --- | --- |
| `ignorar_feriados` | vem do JSON, **default `true`** — igual às séries lançadas à mão |
| data que a regra gera e o JSON não lista | **continua sendo aula**; entra no relatório como "sem conteúdo transcrito" |
| data marcada `sem_aula` | pulada — aí o recesso foi declarado |
| data do JSON que cai em feriado | **erro**, dizendo as duas saídas: `"ignorar_feriados": false`, ou marcar `sem_aula` |

A lição, que vale além desta task: **uma resposta do usuário a uma pergunta estreita
não autoriza inverter um default que o resto do sistema assume.** A pergunta era sobre
uma data; a resposta que eu implementei mudou quem manda no calendário.

**Q3 — nenhum PDF transcrito aqui.** O PR B entrega o comando e os testes; a transcrição
das 3 disciplinas e a migração dos dados seguem no PR C.

## 4. O bug que a implementação achou

**O importador lia o próprio resultado como "não é dia de aula".**

A validação central do comando (§2.5 do plano) é: *toda data do JSON tem de ser uma data
que a série realmente gera* — senão a `Ocorrencia` fica no banco e nunca aparece. O
primeiro corte respondeu a essa pergunta chamando `recurrence.expandir`.

Só que `expandir` **aplica os overrides**, e devolve `None` para uma data `PULADO`. Então:

1. a 1ª execução marca 07/09 como `PULADO` (o JSON diz `sem_aula`);
2. a 2ª execução pergunta a `expandir` quais são os dias de aula;
3. 07/09 não está na resposta — foi a 1ª execução que o tirou de lá;
4. **a 2ª execução recusa o mesmo arquivo que a 1ª aceitou.** Fim da idempotência.

O conserto foi separar as duas perguntas, que nunca foram a mesma:

- **"o que aparece no calendário?"** → `expandir`, com overrides aplicados;
- **"este dia é dia de aula?"** → `datas_da_regra`, a regra crua.

As duas dividem `_rrule_do_evento`, para não haver dois lugares construindo a rrule e
divergindo depois. `test_datas_da_regra_mantem_a_data_pulada` e
`test_segunda_importacao_nao_rejeita_o_proprio_resultado` são a regressão.

## 5. Duas armadilhas que custaram tempo

**`full_clean()` levanta `EscopoAusente` nos models-raiz.** A validação de unicidade do
Django passa pelo manager **default** — que nestes models é o `EscopoManager` da 0B.10 e
recusa consulta sem dono. `Evento` e `RegraRecorrencia` usam
`full_clean(validate_unique=False)`; a unicidade continua garantida pela constraint no
banco, que é mecanismo mais forte (princípio 9). **`Ocorrencia` mantém `full_clean()`
cheio** — ela não tem `EscopoManager`, e é o `full_clean` que aciona a checagem de dono
da `classe_override` do PR A.

**As 5 classes padrão já existem no perfil local dos testes** (migration `0002` +
`seed_classes_padrao`). Uma fixture que as *cria* esbarra em `uq_classe_dono_nome`; a
daqui as **busca**. Quem for escrever teste que precise de classe por nome, use o helper
`_classe()`.

## 6. O que foi verificado

- **Suíte: 487 → 533.** 42 testes do comando + 4 de `datas_da_regra`. `ruff`, `black
  --check` e `makemigrations --check` limpos.
- Os testes cobrem: o formato da série (título, classe, `rastrear_conclusao`,
  `data_fim`), **hora local e não UTC**, `ignorar_feriados=True` por default e a
  disciplina que declara `false`, as duas recorrências do caso ASL com cada data indo
  para o evento do dia certo, conteúdo/prova/entrega/sem aula, o dia sem conteúdo que
  **continua sendo aula**, a **recusa** de data em feriado quando a série os ignora,
  recusa de data em dia da semana errado e além do `data_fim`, atomicidade da recusa,
  idempotência (inclusive após renomear), edição e remoção de linha do JSON, encolher o
  semestre, preservação de `CONCLUIDO`, o dry-run (que valida de verdade), o
  `--substituir` (prefixo, período, classes, e a **recusa** de avulso com estado), a
  validação do JSON, e o isolamento por dono (classe do vizinho não é emprestada).
- **Contra o banco real, em transação com rollback:** importada uma transcrição parcial
  de Redes. A agenda — pelo mesmo `eventos_na_janela` que a API usa — mostrou as aulas
  com o conteúdo do dia e o dia de prova com a **classe `Prova`** e o título
  sobrescrito. Segunda execução: 1 série, não 2.
- **Teste humano, 19/08/2026:** aplicada uma disciplina fictícia às sextas no banco
  real, conferida no calendário pelo usuário, e **removida depois** — banco de volta a
  166 eventos e 2 ocorrências (as duas do usuário). Foi esse teste que expôs o erro do
  auto-PULADO descrito no §3.
- **Backup antes de migrar:** `backup-planejador-20260819-2138.sql`, na raiz de
  `Projetos/Planner/`.
- **A migration `0012` FOI aplicada ao banco real** (o `--aplicar` é que não rodou). O
  schema está à frente do dado, de propósito.

## 7. Onde a próxima começa

**PR C — transcrever os 3 PDFs e migrar os dados.** Roteiro no §3.6 do plano-pai, com
três coisas que este PR mudou ou confirmou:

1. **A série manda em quando há aula; o JSON só traz o conteúdo.** Uma data esquecida
   na transcrição **não** some do calendário — vira "dia de aula sem conteúdo
   transcrito" no relatório. Confira esse contador contra o número de recessos que o
   PDF realmente tem — ele é a régua da transcrição. E marque `sem_aula` nos recessos:
   é o único jeito de tirar um dia do calendário.
2. **`--substituir` recusa avulso com estado do usuário.** Em 19/08 os 67 estavam
   limpos (0 concluídos, 0 com ocorrência, 0 com `origem_tarefa`), mas o usuário está
   usando o sistema — se ele concluir uma aula até lá, o comando **para** e é preciso
   resolver à mão. É de propósito.
3. **ASL são duas recorrências** no mesmo JSON (`dias: [0]` e `dias: [3]`), com durações
   diferentes. O comando já roteia cada data para o evento do dia certo.

Depois da importação vem o **replanejar do zero** (decisão D6 do gate da 1.2) — os 92
blocos promovidos são refeitos pelo solver.

**O `--aplicar` nunca rodou contra o dado real.** Nada do calendário do usuário mudou
neste PR: os 58 avulsos de `Aula` e os 9 de `Prova` continuam lá.
