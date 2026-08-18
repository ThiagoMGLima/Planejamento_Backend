# Contexto — Fase 1.2 / PR A: conteúdo por ocorrência

*Fechado em 18/08/2026. Plano:
[`fase1-aula-fixa-conteudo-por-ocorrencia.md`](fase1-aula-fixa-conteudo-por-ocorrencia.md)
(gate respondido no §7).*

## 1. O que era para fazer

Dar à `Ocorrencia` onde pendurar o **conteúdo de uma data** de uma série recorrente, e
resolver esse conteúdo na leitura. É o PR que destrava a regra: *a aula é bloco fixo; o
que varia por semana é o conteúdo daquela data — inclusive "hoje tem prova" e "hoje
entrega o trabalho"; o esforço nunca é aula, é `Tarefa` com deadline*.

O que motivou: as 3 disciplinas com PDF de planejamento de ensino foram lançadas como
**58 eventos avulsos + 9 provas como evento próprio**, porque não havia esse lugar. As
outras 4 são séries corretas — e os PDFs delas ainda vêm.

## 2. O que foi feito

| Onde | O quê |
| --- | --- |
| `models.py` | `Ocorrencia` ganha `titulo_override` (CharField), `descricao_override` (TextField) e `classe_override` (FK PROTECT). `clean()` recusa classe de outro dono |
| `migrations/0011_conteudo_por_ocorrencia.py` | aditiva, 3 colunas, sem default problemático |
| `services/recurrence.py` | `OcorrenciaView` ganha `titulo`/`descricao`/`classe` **efetivos**; `montar_ocorrencia` os resolve; `prefetch_ocorrencias()` novo |
| `services/agenda.py`, `services/planejamento.py` | passam a usar `prefetch_ocorrencias()` |
| `views.py` | `_payload_ocorrencia` sobrescreve `titulo`, `descricao` e `classe` (serializada), junto com o que já sobrescrevia |
| `services/agente.py` | o resumo da agenda lê o `alvo` (ocorrência quando existe), não o evento |
| `admin.py` | `OcorrenciaAdmin` mostra/filtra/busca o conteúdo — é o único caminho de escrita à mão até o PR D |
| `tests/test_conteudo_por_ocorrencia.py` | 14 testes novos |

**Contrato externo:** inalterado. Nenhum campo novo no payload; campos que já existiam
passam a vir com outro valor em algumas ocorrências.

## 3. Decisões de desenho

**Vazio herda, e por isso `blank=True` sem nulo.** String vazia é "não disse nada", não
"apague o título da série". Evita a terceira sentinela (`None` vs `""` vs ausente) num
model que já tem 3 overrides.

**A resolução mora em `montar_ocorrencia`, uma vez.** A view e o agente leem o
resultado. Se cada consumidor resolvesse, dois consumidores discordariam — e foi
exatamente o bug que o agente tinha: narrava a classe do **evento**, então diria "Aula"
num dia de prova. Corrigido no mesmo PR.

**A prova chama atenção pela CLASSE, não por tratamento visual.** `EventBlock.jsx`
declara: *"a COR vem sempre da classe; o ESTADO é tratamento (borda/opacidade/ícone),
nunca outra matiz"*. Borda e ícone já são de pendente/concluído — usá-los para "tem
prova" colidiria com a única sinalização de estado que o calendário tem.

**`classe_override` é PROTECT**, como `Evento.classe`: apagar a classe `Prova` com dias
de prova pendurados tem de doer, não sumir.

**`clean()` valida o dono da classe.** `Ocorrencia` não tem `dono` para o manager
escopar, e `classe_override` é a **primeira FK daqui para um model por-dono** — sem a
checagem, seria o caminho por onde a classe de um perfil apareceria no calendário de
outro. Vale onde `full_clean` roda (admin, importador); a guarda real continua sendo o
escopo de quem monta a query.

**O invariante mudou, e está reescrito.** "Ocorrência só existe quando o usuário toca a
data" virou "existe por dois motivos: o usuário tocou, **ou** a data tem conteúdo". Está
no docstring do model e no `CLAUDE.md`. Um semestre importado deixa ~120 linhas ali sem
ninguém ter clicado em nada — quem ler "existe linha" como "o usuário mexeu" erra.

## 4. A armadilha de performance (e como não repetir)

O primeiro corte fez `expandir` chamar `evento.ocorrencias.select_related(...)`. Isso
**descarta o prefetch** de quem montou a query — qualquer modificação do queryset ignora
o cache —, então viraria 1 query por evento recorrente. Sem `select_related` nenhum,
viraria 1 query por dia de prova ao resolver a classe.

As duas coisas juntas viraram `recurrence.prefetch_ocorrencias()`: um `Prefetch` com o
`select_related` dentro. `expandir` voltou a usar `.all()`, e quem chama passa o helper.
**Vai chamar `expandir`? use o helper** — é o que mantém a janela em 4 queries.

Medido no banco real (janela de 92 dias, 213 itens): **4 queries com 0 ocorrências e 4
com 21 ocorrências de override**. Constante.

## 5. O que foi verificado

- **Suíte: 473 → 487.** `ruff` e `black --check` limpos, `makemigrations --check` sem
  pendência.
- Os 14 testes novos cobrem: herança sem override, conteúdo só naquela data, prova
  trocando título+classe, override não contaminando a série, string vazia herdando,
  `PULADO` vencendo o conteúdo, o payload da API (inclusive a cor), evento avulso
  intacto, o resumo do agente, `concluir` **não** apagando conteúdo, conteúdo em data já
  concluída reusando a mesma linha (`unique (evento, data)`), classe de outro dono
  recusada, e o solver continuando a ocupar o horário no dia de prova.
- **Contra o dado real, em transação com rollback:** criada uma ocorrência de prova em
  24/08 na série de Física Teórica 4 — só aquela data mudou (`FIS7F4 — Prova 1`, classe
  `Prova`), 18/08 e 25/08 seguiram `Aula`, e o agente narrou "Prova". Banco intacto no
  fim (0 ocorrências).
- **Backup do banco real antes de migrar:** `backup-planejador-20260818-1825.sql` na
  raiz do projeto (o anterior era de 14/08).

## 6. Onde a próxima começa

**PR B — `manage.py importar_planejamento_ensino`.** Não existe nada dele ainda. O
desenho está no §3.5 do plano: entrada é um JSON por disciplina em
`planner/fixtures/planejamento/`, idempotente por (disciplina, semestre), `--substituir`
para trocar os avulsos pela série, e **não cria `Tarefa`** (o esforço já está nas 41 que
existem; misturar as duas escritas faria dele o lugar da próxima duplicata).

Duas coisas que o PR B tem de respeitar e são fáceis de esquecer:

1. **ASL não cabe em uma `RegraRecorrencia`** (confirmado no gate, D7): seg 15:50–17:30
   e qui 15:50–18:40 — mesma hora, durações diferentes, e a regra guarda um horário só.
   São **dois eventos**, como a academia. O importador tem de aceitar N recorrências por
   disciplina e mandar cada data para o evento do dia da semana certo.
2. **Chame `full_clean()` ao gravar `Ocorrencia`** — é o que aciona a checagem de dono
   da `classe_override`.

Depois vêm o **PR C** (transcrever os 3 PDFs e rodar a migração de dados: apagar os 67
avulsos, criar as séries; **replanejar do zero** em seguida — decisão D6) e o **PR D**
(edição pela UI, se e quando).

**Escrita pela API não existe** (decisão D3): até o PR D, quem escreve conteúdo é o
importador e o admin.
