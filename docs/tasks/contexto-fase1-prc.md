# Contexto — Fase 1.1 / PR C: texto livre, perguntas e vocabulário

> **Documento de contexto (passo 7 do ciclo).** Escrito depois de implementar.
> Plano: [`fase1-parametros-por-tarefa.md`](fase1-parametros-por-tarefa.md).
> Anteriores: [PR A](contexto-fase1-pra.md) · [PR B](contexto-fase1-prb.md).
> Data: 15/08/2026. Branch: `claude/fase1-pra-parametros-tarefa`.

## 1. O que era para fazer

Camada B do desenho: a IA lê o texto livre da tarefa e o **traduz** para os knobs
estruturados do PR A; o plano passa a devolver `leitura` (o que foi entendido) e
`perguntas` (até 3, priorizadas); e o vocabulário voltado ao usuário passa a ser
garantido por tabela + teste, não por prompt.

## 2. O que foi feito

| Arquivo | Mudança |
| --- | --- |
| `services/vocabulario.py` | **novo** — knob → frase em português, escrita por código |
| `services/planejamento.py` | `TarefaEntrada.descricao`; diretriz da IA **preenche só o que a tarefa deixou nulo** |
| `services/planejamento_ia.py` | `_ajustes_de_agendamento` (guarda dos knobs novos), `validar_perguntas`, `leitura_das_descricoes`, contexto com `observacao_do_usuario` + `ja_definido`, prompt e schema |
| `services/tarefas.py`, `services/agente.py` | `criar_tarefa` aceita `estrategia`; `SYSTEM_PROMPT` ganha a regra de linguagem |
| `services/replanejamento.py` | `descricao` no `SimpleNamespace` do pool |
| `tasks.py` | `leitura` e `perguntas` no resultado do job (chaves existem também no fallback) |
| `tests/test_vocabulario.py`, `tests/test_descricao_para_knobs.py` | **novos** — 69 testes |

**Suíte: 353 → 422.** `ruff`, `black --check` e `makemigrations --check` limpos.

## 3. Decisões de desenho

### 3.1 Quem escreve o texto é o código

`vocabulario.py` traduz knob → frase. O modelo escolhe **qual** knob propor; a
redação nunca passa por ele. Vale para a `leitura` e para o texto das
`perguntas` — por isso `validar_perguntas` recebe `{tarefa_id, knob, valor,
impacto}` e **descarta** pergunta cujo knob não renderiza frase: sem texto do
código, não há pergunta.

Motivo empírico, não estético: o prompt do planejador já proibia id e nome de
campo **em maiúsculas**, e o agente respondeu ao usuário com
`classe_id: c9a351f9-…`. Prompt é pedido; tabela é garantia.

### 3.2 A guarda de linguagem é teste

`assert_sem_jargao` roda sobre tudo o que vai ao usuário (regex de UUID + lista
de nomes de campo/ferramenta). **Tem teste do próprio teste** — uma guarda que
nunca falha passa a aprovar tudo, então há um caso que exige que ela rejeite a
frase literal que vazou em 15/08/2026. É o princípio 9: nunca abaixo de "falha
no teste".

A regra de linguagem também foi **portada** para o `SYSTEM_PROMPT` do agente, com
teste afirmando que os dois prompts a contêm. Prompt não basta, mas a ausência
dele era um convite.

### 3.3 Precedência: explícito vence inferido

Em `montar_plano`, a diretriz da IA só preenche o knob que a **tarefa deixou
nulo**. Uma leitura errada pode *acrescentar* condição (e é sempre reportada),
nunca *desfazer* o que alguém setou de propósito. É o que mantém a camada B como
tradutora da camada A, e não um canal paralelo.

### 3.4 Leitura sempre reportada — inclusive vazia

Tarefa **com** descrição e **sem** knob inferido aparece com `entendi: []`. "Li e
não tirei nada" é informação: foi a condição para reusar um campo visível
(`descricao`) como entrada de planejamento (D3). Sem ela, a diferença entre "não
havia nada para entender" e "entendi errado e mudei sua agenda" seria invisível.

### 3.5 Ortogonalidade também no guarda-corpo

Cada knob é validado isoladamente e o inválido some sozinho. A **única** checagem
cruzada é o par de datas contraditório (`nao_antes_de > nao_depois_de`), porque
aceitá-lo produziria janela vazia e mataria a tarefa inteira — pior que ignorar
a leitura. A janela horária é validada como par: meia janela não é janela.

## 4. Verificação com o modelo real — e o resultado honesto

O encanamento está correto e é seguro por construção. **Mas o `qwen2.5:7b` não o
exercita.** Duas rodadas contra o banco real (com rollback), com a descrição
*"Só consigo estudar de manhã, e quero deixar para perto da prova"*:

| Rodada | Saída bruta do modelo | Validado |
| --- | --- | --- |
| prompt como escrito | `{"prioridades": {"<uuid inventado>": 1}, "max_min_por_dia_total": 300}`, `perguntas: null` | `{}` |
| instrução movida para o fim, mais imperativa | `{"prioridades": {"<título no lugar do id>": 1}, ...}` | `{}` |

Ele emite **só os knobs antigos** e ignora a instrução nova. Não é posição no
prompt. É o mesmo teto medido em 15/08/2026: o 7b executa instrução explícita e
única, e não compõe.

**O que isso prova a favor do desenho**, e vale registrar:

- o guarda-corpo descartou **as duas** alucinações de id (um UUID inventado e um
  título usado como chave) sem levantar;
- a `leitura` reportou `entendi: []` — honesto, e não uma mudança silenciosa;
- o plano saiu completo e correto do mesmo jeito (princípio 6).

**O que isso significa para o produto:** a camada B só entrega valor com um
modelo mais forte. Ou seja, a utilidade do PR C está amarrada à decisão **D6**,
ainda aberta. Não considere a Fase 1.1 "entregue ao usuário" enquanto o provider
do planejamento for o 7b local — o mecanismo existe, mas fica ocioso.

## 5. Estado em que a próxima task começa

**Pronto:** os 4 PRs de mecanismo (A, B, C) com 422 testes. O ciclo completo
existe: campo estruturado → solver, texto livre → knobs → leitura reportada →
pergunta opcional, tudo com vocabulário garantido.

**Não feito:**

- **PR D** — handoff da conversa forte. Bloqueado em **D6** (privacidade) e
  **D7** (onde vive o JSON único).
- **O front não consome** `leitura` nem `perguntas` — ver §6.
- **Responder uma pergunta** ainda não tem endpoint: a resposta deveria gravar o
  knob na tarefa (fechando o ciclo na camada A, para a pergunta não voltar). Hoje
  o front teria de fazer `PATCH /tarefas/{id}/` com o campo — o que funciona,
  mas não é um caminho desenhado. Candidato a PR C2 ou a entrar no D.
- `cenarios.py` e `refinar` **não** foram tocados: continuam sem os knobs novos.

## 6. Passo 8 — sincronia com o frontend

**Mudança aditiva no resultado do job de IA.** `GET /planejamento/planejar-ia/{job_id}`
passa a trazer duas chaves novas, **sempre presentes** (vazias quando
`ia_indisponivel: true`, para não haver dois shapes):

```jsonc
"leitura": [
  {"tarefa_id": "…", "tarefa": "Estudar para a PP1", "entendi": ["só de manhã"]}
],
"perguntas": [
  {"tarefa_id": "…", "knob": "estrategia", "valor": "TARDE",
   "texto": "Quer que «Estudar para a PP1» estuda perto do prazo?", "impacto": 9}
]
```

Para o agente do frontend:

- **`leitura`** deve ser exibida quando não vazia — é a trava que torna aceitável
  usar a descrição visível como entrada de planejamento. Um item com
  `entendi: []` significa "li a descrição e não tirei nada dela", e vale mostrar.
- **`perguntas`** é opcional e **nunca bloqueia**: o plano já vem completo com o
  default aplicado. São no máximo 3, já ordenadas por impacto. Cada uma tem de
  poder ser respondida ou ignorada **isoladamente**.
- Ao aceitar uma pergunta, grave o knob na tarefa (`PATCH /tarefas/{id}/` com
  `{knob: valor}`, campos já existentes desde o PR A) — é isso que impede a
  mesma pergunta de voltar no próximo plano.
- Use o campo **`texto`** como está. Não reescreva, não gere texto próprio a
  partir de `knob`/`valor`: a redação é responsabilidade do backend (há teste
  que barra jargão).
- `POST /tarefas/` passa a aceitar `estrategia` (`"CEDO"|"TARDE"|null`) —
  continua sem entrar em formulário, como no PR A.
