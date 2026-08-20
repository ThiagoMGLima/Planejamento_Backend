# Fase 1.2 / PR B — `importar_planejamento_ensino`

> **Plano (passo 3 do ciclo).** Escrito antes de codar, com as dúvidas explícitas.
> O passo 4 é um **gate**: nada de implementar antes de a seção 6 ser respondida.
>
> Plano-pai: [`fase1-aula-fixa-conteudo-por-ocorrencia.md`](fase1-aula-fixa-conteudo-por-ocorrencia.md)
> (§3.5 desenha a forma; gate respondido em 18/08/2026).
> Estado de onde este PR parte: [`contexto-fase1-2-pra.md`](contexto-fase1-2-pra.md).

## 1. O que é (e o que não é)

Um comando que lê **um JSON por disciplina** e produz, no perfil do usuário: a **série
recorrente** da aula (um ou mais eventos) e uma **`Ocorrencia` por data com conteúdo**
— a aula do dia, o dia de prova, o dia de entrega, o dia sem aula. Idempotente: rodar
duas vezes não duplica, atualiza.

**Não é:** parsear PDF (§3.5 do plano-pai: os 7 PDFs são heterogêneos, transcrever é
uma vez por disciplina por semestre e sai mais barato que um parser frágil).
**Não é** criar `Tarefa` — o esforço já está nas 41 que existem, e misturar as duas
escritas faria deste comando o lugar onde a próxima duplicata nasce.
**Não é** transcrever os 3 PDFs nem migrar o dado real: isso é o PR C.

> **Por que este plano existe, se a 1.2 já passou pelo gate.** O gate de 18/08 aprovou
> o *desenho da regra* e respondeu D1–D7. O §3.5 descreve a **forma** do JSON em cinco
> linhas de prosa — não o schema, não a chave de idempotência, não o que fazer quando
> uma data do PDF não é dia de aula. Como este PR **apaga 67 eventos do calendário que
> o usuário usa todo dia**, as decisões abaixo não devem ser tomadas sozinho.

## 2. Análise — o que o código e o banco dizem hoje

Tudo nesta seção foi **medido em 19/08/2026**, não estimado.

### 2.1 O formato que a série tem de imitar

As 4 disciplinas que já estão certas seguem um padrão único, e o importador tem de
produzir exatamente ele:

| Campo | Valor |
| --- | --- |
| `titulo` | `Nome da Disciplina (CODIGO)` — ex. `Física Teórica 4 (FIS7F4)` |
| `classe` | `Aula` |
| `rastrear_conclusao` | `False` |
| `regra.tipo` | `SEMANAL` |
| `regra.ignorar_feriados` | `True` |
| `regra.data_fim` | `2026-12-17` |

Os 67 avulsos usam **outra** convenção de título: `SIGLA — conteúdo do dia`
(`ASL — Sinais contínuos e discretos`). O separador é sempre ` — ` e o prefixo é
consistente: **ASL 32, Redes 18, EG1 17**.

### 2.2 Os horários no banco são UTC; a grade é local

`TIME_ZONE = America/Sao_Paulo`, `USE_TZ = True`. ASL está gravada como `18:50` e é
`15:50` na grade. **O JSON tem de declarar hora local** e o comando converter — gravar
o número do PDF direto num `DateTimeField` desloca a aula em 3 horas, em silêncio.

### 2.3 A substituição é segura: não há estado de usuário nos 67

Conferido nos 67 avulsos de `Aula`+`Prova`:

| | |
| --- | --- |
| com `status` não-nulo (concluído/remarcado) | **0** |
| com `Ocorrencia` pendurada | **0** |
| com `origem_tarefa` (bloco promovido) | **0** |

Ou seja: apagá-los não perde "eu assisti essa aula" nenhum. **Isto vale hoje e pode
deixar de valer** — o usuário está usando o sistema. Por isso o comando **confere de
novo em tempo de execução** e se recusa a apagar um evento que tenha ganho estado,
em vez de confiar nesta medição.

### 2.4 Os 9 dias de prova caem todos em dia de aula

Verificado data a data: as 9 provas caem no dia da semana e no horário da disciplina.
**É o que torna a 1.2 possível** — se uma prova caísse fora do slot da aula, ela não
poderia virar `Ocorrencia` de uma série e teria de continuar avulsa. O comando valida
isso e recusa a data que não casar, em vez de gravar uma ocorrência órfã.

### 2.5 A armadilha central: ocorrência em data que a série não gera

`recurrence.expandir` gera as datas por `rrule(WEEKLY, dtstart=evento.inicio,
until=regra.data_fim, byweekday=regra.dias)` e **pula feriado** quando
`ignorar_feriados=True`. `montar_ocorrencia` só é chamada para as datas que a regra
produziu.

Consequência: uma `Ocorrencia` gravada numa data que a série **não** gera — dia da
semana errado, data além do `data_fim`, ou feriado — fica no banco e **nunca aparece**.
Sem erro, sem log. É a falha mais provável deste PR, porque a transcrição de um PDF
erra data com facilidade.

**Por isso a validação não é opcional:** o comando expande a série que acabou de montar
e exige que **toda** data do JSON esteja no conjunto gerado.

### 2.6 ASL precisa de dois eventos (D7, confirmado no gate)

Seg 15:50–17:30 (100 min) e qui 15:50–18:40 (170 min): mesma hora, durações
diferentes. `RegraRecorrencia` guarda um horário só, e a duração vem de
`fim - inicio` do evento — uma regra não expressa isso. O importador tem de aceitar
**N recorrências por disciplina** e mandar cada data para a do dia da semana certo.

Isso tem uma consequência que a seção 6 cobra: **duas séries da mesma disciplina não
podem ser distinguidas pelo título**, se as duas se chamarem `Análise de Sistemas
Lineares (ELEQ30)`.

## 3. Desenho

### 3.1 O JSON — `planner/fixtures/planejamento/<sigla>-<semestre>.json`

Fonte da verdade, versionada (D5, respondida no gate):

```json
{
  "disciplina": {
    "codigo": "ELEQ30",
    "nome": "Análise de Sistemas Lineares",
    "sigla": "ASL",
    "classe": "Aula",
    "rastrear_conclusao": false
  },
  "semestre": "2026-2",
  "recorrencias": [
    { "dias": [0], "inicio": "15:50", "fim": "17:30" },
    { "dias": [3], "inicio": "15:50", "fim": "18:40" }
  ],
  "data_fim": "2026-12-17",
  "datas": [
    { "data": "2026-08-20", "conteudo": "Apresentação da disciplina" },
    { "data": "2026-09-17", "conteudo": "Conteúdo das aulas 1 a 5",
      "prova": "Prova Teórica 1" },
    { "data": "2026-11-26", "conteudo": "Laboratório",
      "entrega": "Entrega da Prática 3" },
    { "data": "2026-11-02", "sem_aula": "Feriado — Finados" }
  ]
}
```

- **`dias`** é `0=seg … 6=dom`, o padrão do projeto (`RegraRecorrencia.dias`).
- **Roteamento sem ambiguidade:** cada data vai para a recorrência cujo `dias` contém
  o dia da semana daquela data. Duas recorrências cobrindo o mesmo dia = erro de
  validação, não "a primeira ganha".
- **`conteudo` → `descricao_override`** (D1: semana comum mantém o título fixo da
  disciplina; o conteúdo aparece ao abrir).
- **`prova` → `titulo_override` + `classe_override = Prova`** (D1 e a regra de cor do
  frontend).
- **`entrega` → `titulo_override`, sem tocar na classe** (D2: se entrega e prova
  tiverem a mesma cor, "vermelho" deixa de querer dizer prova).
- **`sem_aula` → `status_override = "PULADO"`**, e o texto vai para
  `descricao_override` (fica o registro do porquê).

### 3.2 O algoritmo

1. Ler e **validar o JSON inteiro** antes de tocar no banco (schema, dias, horários,
   datas duplicadas, recorrências ambíguas).
2. Resolver o dono (`--dono`, default o perfil local — mesma convenção de
   `seed_planejamento`) e a `Classe` pelo nome, no escopo dele.
3. `--substituir`: achar os avulsos da disciplina (§3.3) e **recusar** se algum tiver
   `status`, `Ocorrencia` ou `origem_tarefa`.
4. Criar/atualizar uma `RegraRecorrencia` + um `Evento` por item de `recorrencias`.
5. Expandir cada série no período e montar o **conjunto de datas válidas**.
6. Para cada data do JSON: exigir que esteja no conjunto (§2.5), achar a recorrência
   dona do dia da semana, e gravar a `Ocorrencia` com `full_clean()` — que é o que
   aciona a checagem de dono da `classe_override` (§6 do contexto do PR A).
7. Apagar as ocorrências **daquela série, naquele período, que sumiram do JSON** — é o
   que faz "corrigir uma linha e re-rodar" funcionar de verdade.
8. Relatório: séries criadas/atualizadas, ocorrências criadas/atualizadas/removidas,
   avulsos apagados, e a **conferência de tarefas** (§3.4).

Tudo em `transaction.atomic()`.

### 3.3 `--substituir`: como os avulsos são achados

Filtro: `dono` + `regra_recorrencia__isnull=True` + `classe__nome in {Aula, Prova}` +
`titulo__startswith` do prefixo declarado (`"ASL — "`, derivado da `sigla`) + `inicio`
dentro do período do semestre.

O prefixo é o **único** sinal que o dado atual carrega — não há campo de disciplina no
`Evento`. Por isso o comando **imprime a lista do que vai apagar** e, sem `--aplicar`,
não apaga nada (§3.5).

### 3.4 O que o comando NÃO escreve, mas confere

Não cria `Tarefa`. Mas, se o JSON listar provas e entregas, o comando **relata** as
que não têm `Tarefa` correspondente com deadline naquela data — conferência, não
escrita. É o relatório previsto no §3.5 do plano-pai.

### 3.5 CLI — o padrão destrutivo que o projeto já tem

`marcar_estrategia` estabeleceu a convenção para comando que edita dado real: **sem
`--aplicar` ele só LISTA o que mudaria e sai sem tocar em nada.** Este comando reusa
isso, e com mais razão — ele apaga eventos.

```
python manage.py importar_planejamento_ensino planner/fixtures/planejamento/asl-2026-2.json
    # dry-run: mostra a série, as ocorrências e o que seria apagado

python manage.py importar_planejamento_ensino <arquivo> --aplicar
python manage.py importar_planejamento_ensino <arquivo> --aplicar --substituir
python manage.py importar_planejamento_ensino <arquivo> --aplicar --dono <email>
```

## 4. Testes (passo 6)

Com fixture sintética, sem depender do dado real:

- série criada com título/classe/`rastrear_conclusao`/`ignorar_feriados`/`data_fim`
  no padrão da §2.1, e horário **local** convertido certo (§2.2);
- **idempotência**: rodar 2× não duplica evento nem ocorrência;
- **edição**: mudar o `conteudo` de uma data e re-rodar atualiza no lugar;
- **remoção**: tirar uma data do JSON remove a ocorrência dela;
- data em dia da semana que a série não gera → **erro**, e nada é gravado (§2.5);
- data além do `data_fim` → erro;
- data em feriado, com `ignorar_feriados=True` → o comportamento que a §6/Q3 decidir;
- duas recorrências cobrindo o mesmo dia → erro de validação;
- `prova` vira `classe_override=Prova` e `titulo_override`; `entrega` **não** toca a
  classe; `sem_aula` vira `PULADO`;
- `--substituir` apaga só o prefixo/período/classes certos, e **recusa** avulso com
  status, ocorrência ou `origem_tarefa`;
- sem `--aplicar`, nada é gravado (o teste que prova o dry-run);
- classe de **outro dono** → recusada (o `full_clean` do PR A);
- ASL de duas recorrências: cada data cai no evento do dia certo.

Além da suíte (passo 6 do ciclo): rodar contra o **banco real em transação com
rollback**, como no PR A, e reportar os números apurados.

## 5. Raio de impacto

- **Escrita:** só o comando novo. Nenhum service, view, serializer ou endpoint muda.
- **Contrato HTTP:** inalterado — o passo 9 é "nada a fazer no frontend", pelo mesmo
  motivo do PR A.
- **Schema:** nenhuma migration nova, **exceto se a Q1 for respondida com a opção (b)**.
- **Dado real:** nada é tocado enquanto o PR C não rodar o comando com `--aplicar`.

## 6. Gate — respondido em 19/08/2026

- **Q1 — chave de idempotência: opção (b).** `Evento` ganha `chave_importacao`
  (migration `0012`), única por dono. É o único mecanismo que sobrevive a renomear a
  disciplina e a remarcar o horário — e este comando vai rodar 7 disciplinas por
  semestre.
- **Q2 — aula marcada em feriado acontece no feriado**, sem aviso. **Isso inverte um
  default e é a decisão mais consequente deste PR** (ver §6.1).
- **Q3 — só o comando.** Nenhuma transcrição de PDF entra aqui; fixture sintética nos
  testes, e o PR C segue dono de transcrever as 3 disciplinas.
- **Q4 — confirmado pelo dado**, sem precisar perguntar: o prefixo dos avulsos é
  `ASL — ` (medido: o separador ` — ` parte 100% dos 67 títulos em sigla + conteúdo), e
  `ELEQ30` é `Análise de Sistemas Lineares` na grade do semestre.

### 6.1 A consequência da Q2: o JSON vira a autoridade sobre quais datas têm aula

"A aula marcada no feriado acontece" **não cabe** com `ignorar_feriados=True`, que é o
que as 4 séries atuais usam: esse flag faz `expandir` pular a data, e a aula sumiria do
calendário (§2.5). Então as séries importadas divergem do padrão da §2.1 num ponto, de
propósito:

| | Séries lançadas à mão | Séries importadas |
| --- | --- | --- |
| `ignorar_feriados` | `True` — o calendário de feriados decide | **`False`** — o PDF decide |

E, para que isso não produza aula fantasma num dia sem aula, o comando **marca `PULADO`
em toda data que a recorrência gera e o JSON não lista**. O resultado é que o calendário
passa a bater **exatamente** com o planejamento de ensino: a aula do feriado aparece
porque o PDF a listou, e o recesso some porque o PDF não o listou.

A troca aceita: uma data esquecida na transcrição vira "sem aula" em silêncio, em vez de
aparecer como aula vazia. Por isso o relatório do comando **conta as datas auto-puladas**
— um número alto ali é o sinal de transcrição incompleta.

O texto original das dúvidas fica abaixo, para quem ler o histórico.

### As dúvidas como foram levantadas

### Q1 — Como o comando reconhece a série que ele mesmo criou? *(a que mais importa)*

O `Evento` **não tem onde registrar** "isto veio de importar ASL 2026/2". Sem uma
chave, re-rodar cria uma segunda série e o calendário passa a mostrar a aula duas
vezes. E a §2.6 fecha a saída fácil: ASL tem **dois** eventos, então o título sozinho
não distingue.

| | Opção | Custo | Falha quando |
| --- | --- | --- | --- |
| **(a)** | título distinto por recorrência (`… (ELEQ30) — seg`) e casar por `(dono, titulo)` | zero | o usuário renomeia pelo app; e o sufixo ` — seg` fica feio no bloco do calendário |
| **(b)** | campo `chave_importacao` no `Evento` (`ELEQ30-2026-2-seg`), único por dono | migration `0012` — o plano-pai não previa schema no PR B | nunca, na prática |
| **(c)** | chave natural `(dono, classe, regra.dias, hora de início)` | zero | o horário da disciplina muda no meio do semestre |

**Minha recomendação: (b).** É o único que sobrevive a renomear e a remarcar, e este
comando vai rodar **7 disciplinas × todo semestre** — o modo de falha das outras duas
é uma série duplicada no calendário real, exatamente a bagunça silenciosa que a Fase
1.2 existe para consertar. O custo é uma migration aditiva de uma coluna. Se preferir
não mexer no schema neste PR, **(c)** é melhor que (a): não suja o título e o horário
de uma disciplina muda menos que o nome dela.

### Q2 — Data do PDF que cai em feriado: erro ou aviso?

As séries usam `ignorar_feriados=True`, então a aula **não acontece** em feriado — e
uma ocorrência gravada ali fica invisível (§2.5). Mas o PDF da coordenação pode
legitimamente marcar aula num dia que a BrasilAPI considera feriado, ou o inverso.

**Proposta: aviso alto, não erro** — grava a ocorrência, imprime `ATENÇÃO: 02/11 é
feriado, esta aula não vai aparecer`, e segue. Erro pararia a importação inteira por
uma divergência de calendário que não é do usuário. A alternativa é recusar e obrigar
a marcar `sem_aula` no JSON, o que é mais explícito e mais chato.

### Q3 — O PR B inclui uma disciplina real, ou só fixture sintética?

O plano-pai põe a transcrição no PR C. Mas transcrever **uma** (Redes, que é a menor:
18 avulsos, uma recorrência só) dentro do PR B provaria o comando contra um PDF de
verdade antes de o PR C apagar 67 linhas.

**Proposta: sim, incluir Redes** — como fixture versionada, importada em dry-run e
verificada contra os 18 avulsos que já existem (que são a transcrição anterior e
servem de gabarito). Sem `--aplicar`: nada muda no banco. O PR C segue dono da
migração de dados.

### Q4 — Confirmação factual, antes de eu escrever o schema

O título da série de ASL fica `Análise de Sistemas Lineares (ELEQ30)` — é o padrão da
§2.1 e o nome que aparece na grade. Confirma? E o prefixo dos avulsos a substituir é
exatamente `ASL — ` (com espaço-travessão-espaço)?
