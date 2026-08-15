# Contexto — Fase 1.1 / PR C2: `atualizar_tarefa`

> **Documento de contexto (passo 8 do ciclo).** Plano:
> [`fase1-parametros-por-tarefa.md`](fase1-parametros-por-tarefa.md).
> Anteriores: [PR A](contexto-fase1-pra.md) · [PR B](contexto-fase1-prb.md) ·
> [PR C](contexto-fase1-prc.md). Data: 15/08/2026.

## 1. Por que existe

Incidente no teste humano do PR C. Pedido em linguagem natural — *"faça essas
duas terminarem na véspera da prova"* — fez o agente chamar `criar_tarefa` e
**duplicar as tarefas no banco real** (apagadas na hora; nenhuma gerou evento).

O diagnóstico separou duas coisas que pareciam uma:

- **Não era o modelo.** Não havia ferramenta para editar tarefa existente. A
  única escrita disponível era criar. Um modelo forte teria falhado igual.
- **`replanejar` também não expõe `preferencias`**, então nem a janela era
  alcançável pela conversa.

## 2. O que foi feito

| Arquivo | Mudança |
| --- | --- |
| `services/tarefas.py` | `atualizar`, `validar_parametros_agendamento`, `CAMPOS_ATUALIZAVEIS`, exceções `TarefaDesconhecida`/`ParametrosInvalidos` |
| `serializers.py` | `TarefaSerializer.validate` passou a **delegar** ao service |
| `services/agente.py` | ferramenta `atualizar_tarefa` (+ `_data_simples`, `_hora_simples`) |
| `mcp_server/server.py` | tool `atualizar_tarefa` (PATCH), registrada em `TOOLS` |
| `tests/test_atualizar_tarefa.py` | **novo** — 24 testes |
| `tests/test_mcp_server.py` | +4 testes |

**Suíte: 420 → 448.** `ruff`, `black` e `makemigrations --check` limpos.
Container `mcp` rebuildado: 14 tools.

## 3. Decisões de desenho

### 3.1 A validação desceu para o service — fonte única

A coerência dos parâmetros (janela em par, ordem das datas, faixa dos dias)
estava só no serializer. Com a ferramenta escrevendo os mesmos campos por outro
caminho, manter as duas seria garantir divergência.

A regra mora em `services/tarefas.validar_parametros_agendamento` e o serializer
a chama. A direção da dependência já era essa (`serializers` importa de
`services`; o contrário criaria ciclo, porque `serializers` importa
`services.planejamento`). Há teste afirmando que a API e a ferramenta **recusam
a mesma coisa**.

Ambos validam o estado **efetivo** (o que já está na tarefa, coberto pelo que
está sendo enviado): num PATCH parcial, validar só o delta deixaria passar
combinação inválida formada com o que já estava lá — tem teste.

### 3.2 Nulo é omissão, não apagamento

Argumento `None` é **ignorado**. O modelo omite o que não quis mexer, e um
`None` acidental não pode zerar restrição que alguém pôs de propósito. Para
apagar existe `limpar`, uma lista explícita de nomes de campo — apagar tem de
ser um ato, não um efeito colateral.

### 3.3 `titulo` e `descricao` ficam de fora

Não são atualizáveis por esta ferramenta. São texto do usuário, e desde o PR C a
`descricao` é **insumo de planejamento**: deixar a IA reescrevê-la seria ela
editando a própria entrada, sem que ninguém percebesse. Quem muda esses dois é o
usuário, pela API.

### 3.4 Confirmação escrita pelo código

O retorno traz `resumo` gerado por `services/vocabulario` ("estuda perto do
prazo", "só de manhã"), não paráfrase do modelo — mesma disciplina do PR C, com
teste de ausência de jargão.

## 4. Verificação com o modelo real — a ferramenta não bastou

Repeti o pedido que causou o incidente, agora com a ferramenta disponível e a
descrição dizendo *"Altera uma tarefa que JÁ EXISTE (nunca use criar_tarefa para
isso)"*.

**O 7b chamou `criar_tarefa` de novo** — criou "Ajustar tarefa de Prova" com
classe Prova e prazo inventado, e respondeu *"A tarefa foi atualizada"*. Apagada;
não gerou evento.

Isso **separa definitivamente as duas causas**, que era o objetivo do PR:

- a lacuna de ferramenta **existia e foi fechada** (24 testes provam que editar
  edita, valida e não duplica);
- o que sobra é **seleção de ferramenta**, e aí é o teto do 7b já medido três
  vezes hoje: ele executa instrução explícita e única, e não escolhe entre
  ferramentas parecidas.

Ou seja: a conversa em linguagem natural sobre a agenda continua dependendo da
decisão **D6**. A diferença é que agora, quando um modelo capaz entrar, ele terá
o que chamar.

## 5. Estado em que a próxima task começa

**Pronto:** criar, atualizar, simular, aplicar e replanejar — o ciclo de escrita
está completo nas duas superfícies (agente em processo e MCP).

**Não feito:**

- `replanejar` **ainda não expõe `preferencias`** na ferramenta do agente. Foi a
  segunda metade do pedido que falhou no teste humano ("abra minha janela para
  as 6h"). Não entrou aqui porque preferência é global e por-chamada, não da
  tarefa — merece decisão própria (candidato: guardar preferências no `Perfil`,
  que hoje não existe).
- **Responder uma `pergunta` do plano** continua sendo `PATCH /tarefas/{id}/`
  na mão — agora com paralelo em ferramenta, mas sem endpoint desenhado.
- **PR D** segue bloqueado em **D6/D7**.
- `cenarios.py` e o refino continuam sem conhecer os knobs novos.

## 6. Passo 9 — sincronia com o frontend

**Nada a fazer.** Nenhum endpoint, serializer ou shape de resposta mudou: a
validação foi movida de lugar, mas as mensagens de erro 400 são as mesmas (há
teste). A ferramenta nova vive dentro do agente e do servidor MCP.

Único efeito visível: um turno do agente pode trazer `atualizar_tarefa` em
`acoes[].ferramenta`, com `mudou_estado: true` — comportamento que o front já
trata desde o C4.
