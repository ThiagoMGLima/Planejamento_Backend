# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Backend Django/DRF do **Planejador de Rotina** — roda via Docker, ainda **sem
autenticação** (acesso só em `localhost`). Frontend é um repo separado (SPA Vite):
<https://github.com/ThiagoMGLima/Planejador_Frontend>.

> ⚠️ **"Sem auth" ≠ "single-user".** O PR1 da Fase 0B já passou: existe o model
> `Perfil`, os 8 models-raiz têm FK `dono` **obrigatória**, as constraints são
> por-dono e **todo o caminho de dados já é multi-tenant**. O que falta é só *quem
> diz quem é o usuário*: `services/perfis.perfil_do_request()` devolve sempre o
> perfil local, e o PR2 troca isso pelo JWT do Supabase.
>
> Consequência prática para quem escreve código aqui: **`Evento.objects.all()`
> levanta `EscopoAusente`**. Ver "Escopo por dono" abaixo antes de escrever
> qualquer query.

Fontes da verdade: contrato em `Handoff de Backend - MVP.html`; plano do MVP em
`PLAN.md`; mapa macro de produto em `ROADMAP.md`; notas de design em `docs/tasks/`.

### Convenção de trabalho — ciclo por task

*Revisado em 19/08/2026. **Este texto é a fonte da verdade do ciclo** — leia-o por
inteiro. Na máquina do autor existem as skills `task-iniciar` e `task-fechar`, que o
executam, mas elas moram na raiz de trabalho (`Planner/.claude/skills/`), **fora deste
repositório e de propósito**: são ergonomia local, não parte do projeto. Num clone
novo elas não existem, e nada se perde por isso.*

**1 task = 1 PR**, e cada task percorre três fases separadas por **dois gates
humanos** — o do desenho, antes de codar, e o do teste, antes do PR.

| Fase | Passos |
| --- | --- |
| **Abrir** (`task-iniciar`) | 1. pegar a task no ROADMAP → 2. **analisar contra o código e o banco REAL** → 3. plano escrito **ou** perguntas no chat → **🚦 gate 1** |
| **Fazer** | 4. implementar → 5. **cobertura de testes** (a suíte cobre? senão, escrever) → 6. rodar suíte + lint + migrations → 7. **verificar o que só o agente verifica** |
| **Fechar** (`task-fechar`) | 8. relatório + **roteiro de teste humano** → **🚦 gate 2** → 9. PR → 10. atualizar **todos** os contextos |

**Gate 1 — plano escrito ou pergunta no chat?** Escreva `docs/tasks/<task>.md` e
espere o OK quando a task mexer em **schema**, **apagar ou sobrescrever dado real**,
tiver **mais de uma forma defensável** de ser feita, ou **inverter um default** que o
resto do sistema assume. Caso contrário vá direto ao código, ainda perguntando no chat
o que ficou obscuro. **O agente escolhe e justifica; o usuário pode discordar.**

**Gate 2 — nada de PR antes do OK.** O usuário roda o backend e o frontend e confirma.
Só então vêm o commit, o PR e a atualização dos contextos.

#### Task que toca os DOIS repos: dois agentes em paralelo

*Acrescentado em 19/08/2026.* Quando a task muda backend **e** frontend, ela não é
feita em série (backend → prompt de sincronia → frontend). É assim:

1. **Um plano só, alinhado, antes de qualquer código** — ele passa pelo gate 1 e é a
   fonte comum dos dois lados. O que ele precisa fixar de forma inequívoca é o
   **contrato**: endpoints, forma de request/response, nomes de campo, códigos de
   status. É o único ponto onde os dois agentes se encontram, e divergência ali só
   aparece na integração, quando custa caro.
2. **Dois agentes, um por repositório**, trabalhando **em paralelo**. Cada um
   implementa e roda os testes do seu lado — `pytest`/`ruff`/`black` aqui,
   `vitest`/`eslint` lá.
3. **Quando os dois terminarem, vem o teste de integração**, com backend e frontend
   de pé juntos. O agente faz o que consegue verificar (chamar a API de verdade,
   conferir o payload, rodar os testes dos dois lados); **o usuário faz o teste final
   na interface**.
4. Só então o gate 2, e **um PR em cada repositório**.

O contrato é a interface entre os dois agentes, não a conversa entre eles: se um
precisar mudá-lo no meio, isso volta para o plano e o outro lado é avisado — não se
resolve de um lado só.

Levante as dúvidas **de uma vez**, no gate 1, em vez de gotejá-las durante a
implementação. E quando uma resposta do usuário tiver **consequência técnica que ele
não previu**, diga qual antes de agir — foi o caso do PR B da 1.2, onde "a aula no
feriado acontece" obrigou a inverter `ignorar_feriados` e a tornar o JSON a autoridade
sobre quais datas têm aula.

#### Quem testa o quê — a divisão é rígida

A divisão é rígida e vale para toda task:

**O agente testa tudo o que consegue testar.** Ao terminar a
implementação, verifique se a suíte **já cobre a mudança por inteiro**. Se cobrir e
passar, ótimo — diga isso. Se não cobrir, **escreva os testes**; não devolva ao
usuário nada que possa virar `pytest`. Além da suíte, exercite o que for verificável
por código: chamar o service com dado real (em transação com rollback), rodar o
comando, inspecionar o container, medir com o modelo de verdade. **Se o agente
consegue verificar, o agente verifica** — mandar o usuário conferir o que um teste
provaria é empurrar trabalho.

**O roteiro de teste humano cobre SÓ o que exige um humano.** Entregue-o
**no chat**, direto — nada de arquivo. Só vire `docs/tasks/teste-humano-<task>.md`
se o roteiro for grande demais para uma mensagem, e nesse caso diga o porquê. Ele
contém apenas o que o agente **não** consegue fazer ou julgar:

- **interface**: clicar, arrastar, ver se o calendário ficou legível;
- **julgamento de produto**: "esse plano é vivível?", "faz sentido para a minha
  rotina?" — correção o teste prova, adequação não;
- **qualidade subjetiva de texto** gerado por IA em conversa livre;
- **decisões sobre dado real** que o agente não deve tomar sozinho (aplicar algo
  irreversível, escolher entre dois comportamentos aceitáveis).

O roteiro deve dizer, para cada item: **o que fazer**, **o que observar** e **o que
seria sinal de problema**. Não peça ao usuário para "verificar se funciona" — isso é
teste automatizado mal-feito. Peça para julgar o que só ele pode julgar.

**Roteiro não contém comando que o agente poderia rodar.** Se o item começa com
"rode este comando e veja o resultado", o agente rodou errado: quem roda é ele, e o
roteiro traz o **resultado já apurado e digerido**, com a pergunta de julgamento em
cima. Consultar o banco, medir carga, contar sessões, comparar com o prazo — tudo
isso é do agente. Ao humano cabe **decidir**, não coletar.

**Junto com o roteiro, relate o que você fez**: o que mudou, como, o que a suíte
passou a cobrir e o que você já verificou por fora dela (com os números). O usuário
precisa saber o que já está provado para não repetir.

O documento de contexto existe porque **o contexto é zerado entre tasks**: cada uma fecha com
`docs/tasks/contexto-<task>.md` registrando o que era para fazer, o que foi feito,
as decisões de desenho, os bugs encontrados e como foram corrigidos, e o estado em
que a próxima task começa. Índice em `docs/tasks/README.md`.

Junto com ele, atualize **"Estado atual"** logo abaixo, o `ROADMAP.md`, o
`docs/tasks/README.md` e — quando mudar algo fora do código — o `HANDOFF.md`. Evita o
pior modo de falha deste projeto: um agente sem contexto reimplementando o que já
existe, ou supondo que existe o que não existe. **Documento vivo que contradiz outro
custa caro:** o índice de tasks disse "Bloqueado" por onze dias depois de o Supabase
ter sido provisionado, e foi o que deixou o PR2 parado.

#### O frontend é parte do gate 2

**O backend não vive sozinho.** O frontend é um repo **vizinho**
(`../../frontend/Planejamento_Frontend/`, SPA React+Vite) que consome esta API por
`VITE_API_URL`. Um PR de backend só fecha quando os dois lados estão verdes juntos —
senão o `main` do backend passa a servir um contrato que o frontend ainda não fala.

**Prompt de sincronia (depois dos testes do backend, nunca antes).** Assim que a
suíte do backend passar, produza um **prompt para o usuário mandar ao agente do
frontend**. O prompt deve:

- dizer que o frontend está no **repositório vizinho**
  (`../../frontend/Planejamento_Frontend/` a partir daqui);
- descrever as mudanças de contrato de forma **concreta e acionável**: endpoints
  novos/alterados, formas de request/response, parâmetros, códigos de status;
- **ou**, quando o PR não muda o contrato externo (foi o caso do PR1), dizer isso
  **explicitamente e com o porquê** — o prompt de "nada a fazer, eis a razão" é um
  artefato válido: dá ao agente do frontend como confirmar que nada quebrou.

O prompt é para o **usuário repassar**, não para o agente do backend executar: o
agente do backend não toca no repo do frontend.

**O PR é gated.** **Não abra o PR** logo após os testes do backend. Ele só pode ser
aberto **depois** que o usuário testar (backend e, quando houver mudança visível,
frontend) e confirmar. Até essa confirmação chegar, o trabalho fica em espera — mesma
disciplina do gate 1: não avance sobre uma premissa que ainda não foi confirmada.

### Onde olhar primeiro (agente sem contexto)

0. **[`docs/tasks/HANDOFF.md`](docs/tasks/HANDOFF.md)** — o que está **fora** deste
   arquivo: portas/modelo desta máquina, o Supabase já provisionado, as armadilhas que
   já custaram investigação e **quais branches ainda não foram mergeadas**. Nada disso é
   dedutível do código.
1. **"Estado atual"**, logo abaixo — o que já existe, para não reimplementar nem
   supor o que não existe.
2. `docs/tasks/README.md` — índice das notas, e qual é a task ativa.
3. `ROADMAP.md` — a ordem das tasks e os princípios (o **nº 9** rege decisões de
   isolamento/segurança).
4. O `contexto-*.md` da última task concluída — onde o trabalho parou e por quê.

### Estado atual — o que já está construído

*Atualizado em 19/08/2026 (fim do PR B da Fase 1.2). **Mantenha esta seção viva:**
ao fechar uma task, mova a linha de "não existe" para cá.*

| Fase | Entrega | Onde |
| --- | --- | --- |
| **MVP** (marcos 1–4) ✅ | models, CRUD, recorrência via `rrule`, feriados, `status_efetivo` derivado, `concluir`/`remarcar`, testes + CI | `models.py`, `views.py`, `services/recurrence.py`, `services/completion.py` |
| **A — Planejamento** ✅ | **solver** multitarefa (EDF guloso + anti-conflito + cascata de relaxamento) e camada de IA que emite **diretrizes** e re-roda o solver | `services/planejamento.py`, `services/planejamento_ia.py` |
| **C — Rotina inteligente** ✅ | cenários com trade-offs + refino conversacional (C1b/C5), replanejar com diff (C2), fatores adaptativos por classe (C3), agente com tool use (C4/C7), estimativa de duração dos jobs (C6), feriados regionais (C8) | `services/cenarios.py`, `services/replanejamento.py`, `services/adaptacao.py`, `services/agente.py`, `services/tempos.py`, `services/holidays.py` |
| **0B / PR0** ✅ | views finas: a regra saiu de `promover`/`planejar` para services; as ferramentas do agente passaram a chamar os services **em processo** (antes era HTTP contra a própria API) | `services/tarefas.py`, `services/agenda.py` |
| **0B / PR1** ✅ | `Perfil`, FK `dono` nos 8 models-raiz, unicidade por-dono, **manager que recusa consulta sem escopo**, posse dos jobs assíncronos, seed de classes por perfil | `managers.py`, `services/perfis.py`, migrations `0007`–`0009` |
| **0A.3** ✅ | **telemetria** das chamadas de IA: 1 registro JSONL por chamada (duração, tokens, `tok_s`, carga de modelo separada) nas 4 famílias — inclusive o **agente**, antes sem medição nenhuma. Nunca grava conteúdo. `LOGGING` passou a existir no settings. `familia` é parâmetro **obrigatório** de `llm.gerar_json` | `services/telemetria.py`, `config/settings.py` |
| **0A.1** ✅ | abstração `LLMProvider` da forma *1 chamada + JSON schema*: providers Ollama/Anthropic/Mock por `LLM_PROVIDER`; os 3 pontos com `ollama.Client` direto agora chamam `llm.gerar_json` | `services/llm.py`, `services/planejamento_ia.py`, `services/cenarios.py` |
| **1.1 / PR A** ✅ | **parâmetros de agendamento por tarefa**: `estrategia` CEDO/**TARDE** (agenda colado no prazo — o solver deixou de só saber "o quanto antes"), pisos/tetos **duros** de data (`nao_antes_de`/`nao_depois_de`, nunca relaxados) e janela/dias **suaves** por-tarefa; comando `marcar_estrategia` | `models.py`, `services/planejamento.py`, `services/replanejamento.py`, `serializers.py` |
| **1.1 / PR C2** ✅ | **`atualizar_tarefa`**: o agente não tinha como EDITAR tarefa (só criar) e duplicava ao ser pedido para alterar. A coerência dos parâmetros desceu para `services/tarefas` como **fonte única** — o serializer delega. Nulo é omissão; apagar exige `limpar` explícito; `titulo`/`descricao` não são atualizáveis pela IA | `services/tarefas.py`, `services/agente.py`, `serializers.py`, `mcp_server/server.py` |
| **1.1 / PR C** ✅ | **texto livre → knobs**: a IA lê a `descricao`, o guarda-corpo valida os knobs novos, e o plano devolve `leitura` (o que foi entendido — sempre reportado) e `perguntas` (até 3, priorizadas). **Vocabulário voltado ao usuário vem de tabela em `services/vocabulario.py`**, não de paráfrase do modelo, com teste que barra UUID/nome de campo. ⚠️ o 7b local **não** exercita isto (ver `contexto-fase1-prc.md` §4) | `services/vocabulario.py`, `services/planejamento_ia.py`, `tasks.py` |
| **1.1 / PR B** ✅ | **`aplicar_plano`**: o par que faltava do `simular_plano` — recalcula com os mesmos argumentos curtos (inclusive `a_partir_de`) e persiste. O plano **não** trafega pelo modelo; segunda chamada não duplica (esbarra em "já promovida"). Espelhado no MCP | `services/agente.py`, `mcp_server/server.py` |
| **1.2 / PR B** ✅ | **`importar_planejamento_ensino`**: um JSON por disciplina vira série recorrente + uma `Ocorrencia` por data. Idempotente por `Evento.chave_importacao`; sem `--aplicar` só SIMULA. **A série manda em QUANDO há aula** (regra + `ignorar_feriados` + `data_fim`); o JSON só traz o CONTEÚDO de cada data. Dia sem conteúdo continua sendo aula e vira aviso; só `sem_aula` pula. Recusa data que não é dia de aula — ela ficaria invisível. Não cria `Tarefa` | `management/commands/importar_planejamento_ensino.py`, `services/recurrence.py`, migration `0012` |
| **1.2 / PR A** ✅ | **conteúdo por ocorrência**: a aula vira bloco fixo recorrente e o que varia por semana (conteúdo, "hoje tem prova", "hoje entrega") mora na `Ocorrencia` — `titulo/descricao/classe_override`, resolvidos na LEITURA. Dia de prova sai na cor da classe `Prova` sem o frontend mudar nada. `prefetch_ocorrencias()` mantém a janela em 4 queries | `models.py`, `services/recurrence.py`, `views.py`, `services/agente.py`, migration `0011` |

**O que ainda NÃO existe** — não assuma nada disto:

- **Autenticação.** Não há login, JWT, `request.user` nem `IsAuthenticated`. Quem
  responde "de quem são os dados" é `services/perfis.perfil_do_request()`, e hoje
  ela devolve sempre o perfil local. É a **única** função que o PR2 troca.
- **Supabase.** Existe só em documentação — zero código, zero dependência.
- **Conta demo e gate de pagamento** (`pode_usar`) — PR3. Os campos `plano` e
  `trial_ate` do `Perfil` existem, mas nada os lê.
- ~~**Abstração `LLMProvider`** (0A.1)~~ — ✅ **feito**: `services/llm.py`
  (`gerar_json`, providers Ollama/Anthropic/Mock por `LLM_PROVIDER`). Os 3 pontos que
  instanciavam `ollama.Client` direto agora chamam `llm.gerar_json`. `AGENTE_PROVIDER`
  (agente, multi-turno) segue **separado** de `LLM_PROVIDER` (planejamento, 1 chamada).
- **Quem LIGA os parâmetros de agendamento, na prática.** O caminho existe inteiro
  (API, `criar_tarefa`, `atualizar_tarefa`, tradução da `descricao`), mas **nada os
  preenche sozinho**: não há default de classe (decisão D2) e o frontend não os
  mostra. Com o 7b local a IA também não preenche — ver o último item desta lista.
  Hoje quem seta de fato é a API, o admin ou `manage.py marcar_estrategia`.
- **Qualquer jeito de criar EVENTO pela interface** (1.3). `addEvento` existe na store
  do frontend e fala com a API, mas nenhum componente o chama — só há
  `NovaTarefaForm`. Evento só nasce por promoção de tarefa, agente, API ou admin, e
  **não há controle de recorrência, `data_fim` ou `ignorar_feriados` em tela alguma**.
- **Os planejamentos de ensino transcritos** (1.2 / PR C). O importador existe, mas
  `planner/fixtures/planejamento/` só tem o `README.md` com o schema — **nenhum JSON de
  disciplina foi escrito**, e o `--aplicar` nunca rodou contra o dado real. Os 58
  avulsos de `Aula` e os 9 de `Prova` continuam no calendário.
- **Escrita de conteúdo de ocorrência pela API/UI** (1.2 / PR D, decisão D3). O PR A fez
  a LEITURA resolver `titulo/descricao/classe_override`; nenhum endpoint os grava.
- **Endpoint para RESPONDER uma pergunta.** O plano devolve `perguntas`, mas aceitar
  uma é `PATCH /tarefas/{id}/` com o knob — funciona, não é caminho desenhado.
- **Preferência PERSISTENTE.** A ferramenta `replanejar` já aceita `janela_inicio`,
  `janela_fim`, `evitar_fds` e `max_min_por_dia`, mas valem **só para aquela
  chamada**: o `Perfil` não guarda preferência nenhuma, e o frontend nunca envia. Na
  prática, quem quiser mudar a janela de forma duradoura ainda depende do `DEFAULTS`
  em `services/planejamento.py`.
- **Cenários e refino não conhecem os knobs novos.** `cenarios.py` segue intocado.
- **Um modelo que USE as ferramentas.** O mecanismo dos PRs C e C2 está pronto e
  testado, mas o `qwen2.5:7b` não o exercita: ignora a instrução de traduzir a
  `descricao` (medido 2×) e, pedido para ALTERAR uma tarefa, chama `criar_tarefa`
  mesmo com `atualizar_tarefa` disponível e a descrição dizendo "nunca use
  criar_tarefa para isso" (medido 15/08/2026, depois do PR C2). Nada quebra — o
  guarda-corpo descarta e a `leitura` reporta `entendi: []` — mas a camada
  conversacional fica ociosa. É o gargalo que a decisão **D6** destrava.
- **Hospedagem.** Roda só local, via compose.

**Onde o trabalho parou:** **1.2 / PR B**, fechado em 19/08/2026 com o ciclo inteiro
(contexto em [`contexto-fase1-2-prb.md`](docs/tasks/contexto-fase1-2-prb.md)). A próxima
task é o **PR C da 1.2**: transcrever os 3 PDFs de `../../aulas/` e rodar a migração dos
dados. Depois dela, a frente que sobra na Fase 1 é o PR D (UI), e a espinha da Fase 0 é
o **PR2**, parado desde 08/08/2026. Detalhe em `docs/tasks/README.md`.

**Suíte:** 533 testes (rodados em 19/08/2026, todos verdes), dos quais 41 de isolamento (`planner/tests/test_isolamento.py`)
— os únicos que provam isolamento, porque usam **dois** perfis; o resto roda com um
só, onde "global" e "do dono" coincidem.

## Layout

Este diretório (`Planejamento_Backend/`, onde mora este arquivo) é a raiz do
projeto Django — `manage.py`, `docker-compose.yml` e `pyproject.toml` ficam aqui;
rode os comandos a partir daqui. O código de aplicação vive em `planner/`, config
em `config/`. (O repositório está aninhado em `.../Projetos/Planner/backend/`, então a
sessão pode abrir um nível acima; o frontend é o irmão `.../Projetos/Planner/frontend/`.)

## Comandos

```bash
# subir tudo (db, redis, ollama, web, celery, mcp) — entrypoint do web faz migrate + collectstatic
docker compose up --build

# baixar o modelo da IA uma vez (a IA é opcional; ver IA abaixo)
docker compose exec ollama ollama pull qwen2.5:7b-instruct

# dados de exemplo (--clear zera tarefas/eventos antes; mantém as classes)
docker compose exec web python manage.py seed_demo --clear           # variado, com histórico
docker compose exec web python manage.py seed_planejamento --clear    # grande, futuro, p/ exercitar o planejador
```

Os dois seeds **não convivem**: `--clear` zera tarefas/eventos, então rodar um
substitui o dataset do outro. Sem `--clear` eles **acumulam** (não são idempotentes).

> **Máquina sem GPU AMD:** o serviço `ollama` do compose está fixado em ROCm
> (`ollama/ollama:rocm`, `/dev/kfd`, `group_add: 990`) para a RX 7600 do desktop. Em
> máquina sem `/dev/kfd` o serviço não sobe — e o `web` depende dele, então a stack
> inteira trava. Solução: um `docker-compose.override.yml` local (gitignorado) com a
> imagem de CPU e `devices: !reset []` / `group_add: !reset []` — o `!reset` é
> obrigatório porque listas em override são **concatenadas**, não substituídas.

### Testes e lint — ATENÇÃO

As imagens `web`/`celery` são de produção e **NÃO incluem as dev-deps** (pytest,
ruff, black, factory-boy — só em `requirements-dev.txt`). É preciso instalá-las no
container antes. Como o `web` monta o código do host e usa `--reload`, um restart
do container **descarta instalações efêmeras** — reinstale se os comandos sumirem.

```bash
# forma limpa (container descartável)
docker compose run --rm web sh -c "pip install -r requirements-dev.txt && pytest"

# ou no container já de pé
docker compose exec -T web pip install -r requirements-dev.txt
docker compose exec -T web python -m pytest                                   # suíte toda
docker compose exec -T web python -m pytest planner/tests/test_planejamento_ia.py
docker compose exec -T web python -m pytest planner/tests/test_planejamento_ia.py::test_endpoint_sem_tarefa_ids_400  # um teste
docker compose exec -T web ruff check .
docker compose exec -T web black --check .
docker compose exec -T web python manage.py makemigrations --check --dry-run  # CI falha se houver migration pendente
```

Os testes **exigem Postgres** (o `dias` de `RegraRecorrencia` é um `ArrayField`
do Postgres — SQLite quebra). O compose já provê o DB. A CI
(`.github/workflows/ci.yml`) roda ruff, black `--check`, checagem de migrations e
pytest contra um Postgres de serviço; sem Redis, o cache cai para locmem.

## Arquitetura

DRF fino: as **views delegam para `planner/services/`**, onde mora a lógica. Os
services importam só de `models`/outros services — **nunca de `views`** (evita
import circular); por isso `montar_plano`/`serializar_plano` vivem em services,
compartilhados pela view `/calcular` e pela task Celery.

### Escopo por dono (`planner/managers.py`) — leia antes de escrever query

O manager default dos 8 models-raiz **recusa consulta sem escopo**. Não é estilo:
é o invariante de isolamento entre contas, no nível "falha no teste" em vez de
"convenção documentada" (princípio 9 do `ROADMAP.md`).

```python
Evento.objects.do_dono(perfil).filter(...)   # o caminho normal
Evento.objects.sem_escopo().filter(...)      # varredura global — só seeds e admin
Evento.objects.all()                         # levanta EscopoAusente
```

Três consequências que pegam de surpresa:

- **`do_dono(None)` levanta** — nunca vira "sem filtro".
- Os **related managers reversos** herdam a guarda: `tarefa.eventos.all()` também
  levanta. Use `Evento.objects.do_dono(...).filter(origem_tarefa=tarefa)`.
- `create()` é livre: quem barra escrita sem dono é o `NOT NULL` do banco.
- **`objects.raw()` é bloqueado** (SQL cru devolve um `RawQuerySet`, que não passa
  pela guarda). A saída explícita é `Model._base_manager.raw(...)`.

Auditado empiricamente: `values`, `values_list`, `aggregate`, `first`, `latest`,
`in_bulk`, `iterator`, `contains`, `dates`, `none()`, `len()`/`bool()` e
`get_or_create` caem todos na guarda. `connection.cursor()` continua fora do
alcance de qualquer manager — é o limite da técnica, não um esquecimento.

A identidade é **parâmetro do domínio, nunca ambiente**: os services recebem
`dono` como argumento obrigatório (nada de `contextvar` — no worker Celery ele
seria `None`, o filtro sumiria em silêncio e a suíte, que roda com um perfil só,
passaria verde). Quem resolve o dono é `services/perfis.perfil_do_request()`.

### Models (`planner/models.py`)
`Perfil` (a conta) e, com FK `dono` **obrigatória** para ele, os 8 models-raiz:
`Classe`, `Tarefa` (Inbox), `Evento` (calendário), `RegraRecorrencia`,
`PesoPreferencia` (peso aprendido por métrica de cenário, EWMA), `EscolhaCenario`
(lote cru de cenários + qual foi escolhido, permite recalcular o aprendizado do
zero), `RegistroExecucao` (alimenta os fatores adaptativos) e `FeriadoLocal`
(feriados municipais mantidos à mão). `Ocorrencia` é a única sem `dono`: herda
pelo `evento` (CASCADE, não-nulo). Todos herdam de `TimestampedModel`.

Três invariantes que atravessam o código:
- **`PENDENTE` é derivado na leitura, nunca gravado** (status efetivo calculado em
  `services/completion.py`).
- **Ocorrências de eventos recorrentes são virtuais**: a expansão é sob demanda e
  nada materializa calendário. Mas a linha `Ocorrencia` existe por **dois** motivos
  (o 2º entrou na Fase 1.2): o usuário TOCOU a data (conclui/remarca/pula), **ou** a
  data tem CONTEÚDO próprio (`titulo/descricao/classe_override`) — importado, sem
  ninguém ter clicado. Não leia "existe linha" como "o usuário mexeu".
- **Unicidade é sempre por-dono** (`Classe.nome`, `PesoPreferencia.metrica`,
  `FeriadoLocal`): global, elas impediriam a segunda conta de existir.

### Services

Salvo `recurrence.py`, `tempos.py` e as funções puras do solver, **todo service que
toca o banco recebe `dono` como primeiro parâmetro obrigatório**.

- `perfis.py` — quem é o dono: `perfil_do_request()` (o ponto que o PR2 troca),
  `perfil_local()` e `seed_classes_padrao()` (as 5 classes padrão, que na 0B.6
  saíram da migration 0002 para cá — no migrate não havia para quem semear).
- `recurrence.py` — expande recorrência em ocorrências virtuais via `dateutil.rrule`,
  SEMPRE dentro de uma janela limitada (nunca série infinita). Reusado por
  `EventoViewSet.list` e pelo planejador. **É o único lugar que resolve o efetivo de
  uma data** (horário, status e — desde a 1.2 — título, descrição e classe): quem
  consome lê `OcorrenciaView` e não decide nada. Vai chamar `expandir`? use
  `prefetch_ocorrencias()` no queryset, senão a janela vira N+1.
  **`expandir` responde "o que aparece no calendário", não "este dia é dia de aula"** —
  ele aplica os overrides e omite a data PULADA. Para a segunda pergunta use
  `datas_da_regra()`, que devolve a regra crua; confundir as duas quebrou a idempotência
  do importador uma vez (ver `contexto-fase1-2-prb.md` §4).
- `completion.py` — deriva `PENDENTE`; `concluir`/`remarcar` são as únicas
  transições de escrita (remarcar devolve a `Tarefa` de origem ao Inbox).
- `holidays.py` — feriados via BrasilAPI no servidor, cache agressivo + cópia stale
  para sobreviver a falhas externas. `feriados_do_ano(ano, dono)`: nacional e
  estadual são **fatos globais** (cache compartilhado entre perfis, chamada externa
  cara amortizada); só a camada municipal (`FeriadoLocal`, do DB) é por-dono.
- `planejamento.py` — **solver** de produção multitarefa (guloso EDF + anti-conflito
  + cascata de relaxamento). **Função pura, não persiste** (persistir é do
  `/aplicar`). As preferências são SUAVES: se não couber na janela antes da
  deadline, o relaxamento libera fim de semana → tetos diários → 24h → sessões
  curtas. `montar_plano(...)` é o orquestrador; `horizonte_dias` limita a janela
  (`HORIZONTES`: AUTOMATICO/SEMANA/DUAS_SEMANAS/MES).
- `planejamento_ia.py` — camada de IA **opcional** sobre o Ollama. Pipeline numa
  chamada: `construir_contexto` (só FATOS grounded) → `gerar_melhoria` (1 chamada,
  JSON schema forçado) → `validar_diretrizes` (guarda-corpo: faz clamp/descarte,
  **nunca levanta**) → re-roda o solver com as diretrizes. A IA **não inventa
  números/datas**: só emite diretrizes (`prioridades`, `buffer_dias`,
  `max_min_por_dia`, `max_min_por_dia_total`) que realimentam o solver, buscando
  uma rotina mais "humana" (distribuir esforço, suavizar picos). `estimar_tempo_s`
  alimenta o endpoint de estimativa.
- `cenarios.py` — gera 3–4 cenários com trade-offs e o **refino conversacional**
  (Marcos C1b/C5). Desde a 0A.1 fala com o modelo por `llm.gerar_json`, não mais por
  `ollama.Client` direto. **Não conhece os knobs da 1.1** — segue emitindo o
  vocabulário antigo de diretrizes.
- `adaptacao.py` — aprende `PesoPreferencia` por **escolha revelada** (EWMA). Os pesos
  **ordenam e sugerem** cenários, nunca filtram.
- `replanejamento.py` — replaneja do agora em diante; simula (plano + diff) ou aplica,
  substituindo as sessões futuras.
- `agente.py` — agente conversacional com **tool use multi-turno** (Marco C7). Tem
  abstração de provider **própria** (`_OllamaProvider` / Anthropic + factory por
  `AGENTE_PROVIDER`), separada do `llm.py` da 0A.1 por decisão (24/07/2026): são formas
  diferentes — multi-turno stateful aqui, 1 chamada stateless lá. As ferramentas
  (incluindo `aplicar_plano` e `atualizar_tarefa`, da 1.1) chamam os **services em
  processo** (PR0 da Fase 0B) — antes era `requests` contra a própria API. Cada
  ferramenta recebe `dono` como primeiro **posicional** e o dispatch o passa por fora
  do `**tc.args`: o que o modelo escolhe e a identidade de quem conversa chegam por
  caminhos diferentes, então nenhuma saída do LLM troca o dono dos dados.
- `llm.py` — o `LLMProvider` da 0A.1: `gerar_json(system, messages, schema, familia=...)`
  na forma *1 chamada + JSON schema*, com Ollama/Anthropic/Mock por `LLM_PROVIDER`.
  Usado por `planejamento_ia.py` e `cenarios.py`; o agente **não** passa por aqui.
- `vocabulario.py` — tabela de tradução dos termos internos para linguagem de usuário
  (1.1 / PR C). Existe porque "não usar linguagem técnica" é garantido por tabela +
  teste, não por prompt: nenhum nome de campo nem UUID chega ao usuário.
- `tarefas.py` — `promover`/`planejar`/`criar`/`atualizar` (regra que morava dentro das
  views). Desde o PR C2 é a **fonte única** da coerência dos parâmetros de agendamento —
  o serializer delega para cá.
- `agenda.py` — janela de eventos expandida e pendentes. **Devolve objetos de
  domínio, não DTOs**: assim `services/` não importa `serializers`, e cada consumidor
  monta a própria forma (a view faz o JSON do contrato; o agente, o resumo digerido).
- `aplicacao.py` — persiste as sessões do plano revisado (`/aplicar`).
- `tempos.py` — estimativa adaptativa de duração dos **jobs** de IA (Marco C6). Escalar
  EWMA no cache, a serviço da contagem regressiva do front. **Não confunda com
  `telemetria.py`** (abaixo): aquele mede a chamada e persiste; este estima o job e é
  volátil. Não unifique os dois — ver decisão Q3 em `contexto-0a3-instrumentacao.md`.
- `telemetria.py` — **registro** das chamadas de IA (0A.3): 1 linha JSONL por chamada com
  duração, tokens, `tok_s`, carga de modelo separada, `ok`/`erro` e `dono`. É o dado da
  Fase 2 (IA local vs API) e da 0A.5. Duas regras que não podem regredir: **nunca grava
  conteúdo** (prompt, resposta, título de tarefa) nem atrás de flag, e **nunca derruba a
  chamada de IA** — falha ao registrar vira `warning`.

### Fluxo assíncrono (Celery)
`planner/tasks.py` tem **4 jobs**: `planejar_ia_task`, `gerar_cenarios_task`,
`refinar_cenario_task` e `agente_chat_task`. Todos seguem o mesmo padrão: a view valida
síncrono e enfileira → responde **202 `{job_id}`** (ou 200 se já em cache) → o front faz
polling no `GET .../{job_id}`. Todos recebem `dono_id` como **primeiro argumento** do
payload. Resultado cacheado no Redis por uma chave derivada da entrada (no planejar-ia:
`dono + tarefa_ids + prefs efetivas + plano base`). Se o Ollama falhar
ou `IA_PLANEJAMENTO_ENABLED=0` / `AGENTE_ENABLED=0`, degrada para o plano base do solver
com `ia_indisponivel: true` — **a IA nunca é caminho crítico**.

## Convenções da API

Rotas do router (`/classes/`, `/tarefas/`, `/eventos/`) exigem **barra final**;
`/classes/` e `/tarefas/` são paginadas por cursor. As rotas avulsas (`/health`,
`/pendentes`, `/feriados`, `/planejamento/*`) são `path()` **sem** barra final.

Formatos de resposta: `/eventos/` e `/pendentes` retornam **arrays**; `/feriados`
retorna um **objeto** `{ano, feriados: [...]}`. Ações do router: `promover` e
`planejar` em `/tarefas/{id}/`, `concluir` e `remarcar` em `/eventos/{id}/`
(`?escopo=ocorrencia|serie`).

Em `/planejamento/` há 4 famílias: `calcular`/`aplicar` (síncronas), `planejar-ia`
(+`estimativa`, +`{job_id}`), `cenarios` (+`escolher`, `refinar`, `{job_id}`) e
`agente/chat` (+`{job_id}`), além de `replanejar` (+`aplicar`). **Ordem importa em
`urls.py`**: `cenarios/refinar` vem antes de `cenarios/<job_id>`, senão casaria como
job_id.

**Job não é credencial.** Todo endpoint que dereferencia um `job_id` confere a posse
(`views._registrar_dono_job` / `_job_do_dono`, chave `job_dono:{id}` no cache) e
responde **404** se o job for de outro perfil ou desconhecido. Enfileirou um job? tem
de registrar o dono junto — inclusive no caminho de cache-hit, que cria um `job_id`
novo. Objeto de outro perfil também é **404**, e FK cruzada é **400 "inexistente"**:
a resposta não deve deixar distinguir "não existe" de "existe e não é seu".

## IA / Ollama

O compose roda o Ollama na **GPU AMD via ROCm** (RX 7600 / gfx1102, commit `cc2a130`:
7,4 → 47 tok/s). Em máquina sem `/dev/kfd` cai para CPU via override (ver Comandos), na
casa de dezenas de segundos por plano. O modelo fica residente
(`OLLAMA_KEEP_ALIVE=-1`) para evitar cold start.

Variáveis (ver `.env.example`):
- **Planejamento:** `IA_PLANEJAMENTO_ENABLED`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`
  (default `qwen2.5:7b-instruct`), `OLLAMA_TIMEOUT`.
- **Estimativa:** `PLANEJAR_TEMPO_BASE_S`, `PLANEJAR_TEMPO_POR_TAREFA_S`.
- **Agente:** `AGENTE_ENABLED`, `AGENTE_PROVIDER` (`ollama|anthropic`), `AGENTE_MODEL`,
  `ANTHROPIC_API_KEY`. **Não há `API_BASE_URL` no settings** desde o PR0 — só o
  `mcp_server/` usa essa variável, lida do ambiente pelo serviço `mcp` do compose.
- **Feriados:** `FERIADOS_UF` (camada estadual offline; vazio desliga).

## Telemetria e logging (0A.3)

`llm.gerar_json` exige **`familia`** (`planejar_ia|cenarios|refino|agente`) como
parâmetro nomeado — não é opcional. Mesma razão pela qual o `dono` não virou
`contextvar` na 0B.10: **identidade é parâmetro do domínio, nunca ambiente**; contexto
implícito falharia em silêncio no worker Celery. Se você adicionar um call site novo,
passe `familia` e, quando houver, `dono_id`.

Onde ler o que foi registrado:

```bash
tail -f .telemetria/llm.jsonl                       # ao vivo
jq -s 'group_by(.provider + "/" + .modelo)[] |
  {chave: .[0].provider + "/" + .[0].modelo, n: length,
   tok_s: (map(.tok_s // empty) | add / length),
   falhas: (map(select(.ok == false)) | length)}' .telemetria/llm.jsonl
```

O arquivo é escrito **como root** pelo container (bind mount `.:/app`); para apagar,
`docker compose exec web rm -rf /app/.telemetria`. Está no `.gitignore`.

> ⚠️ **`LOGGING` só é aplicado por `django.setup()`.** Testar log com
> `docker compose exec web python -c "..."` **não** funciona — `python -c` não chama
> `django.setup()`, então a config do settings nunca entra e o `logger.info` some. Use
> `manage.py shell -c`. Isso já custou uma investigação; não repita.

> A suíte roda com a telemetria **desligada** (fixture autouse em `tests/conftest.py`).
> Sem isso os testes escrevem registros sintéticos no JSONL real — e esse arquivo é base
> de decisão de produto, não log descartável.

## Servidor MCP

O serviço `mcp` do compose (`mcp_server/`, fora do Django) expõe as ferramentas do
backend via Model Context Protocol em `http://localhost:8765/mcp` — camada fina sobre a
API HTTP, **zero lógica de domínio**. Chama a API por `API_BASE_URL`, hoje sem
autenticação, e portanto opera no perfil local (é quem a API resolve). É a **única**
fronteira HTTP que sobra — o agente passou a chamar os services em processo.

A credencial de serviço prevista para ele **fica no PR2**, não por esquecimento: não há
o que autenticar enquanto a API não tem autenticação nenhuma. Ela chega junto com o
`SupabaseJWTAuthentication`, no mesmo PR que inverte o `DEFAULT_PERMISSION_CLASSES`.
