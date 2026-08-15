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

**1 task = 1 PR**, e cada task percorre este ciclo (detalhe em `ROADMAP.md`,
seção "Método de trabalho"):

1. Task (próximo item do ROADMAP) → 2. **Análise do código atual** → 3. **Plano de
implementação** em `docs/tasks/`, com as dúvidas explícitas → 4. **Revisão do usuário
+ sanar dúvidas** → 5. Implementação → 6. **Testes automatizados** → 7. **Roteiro de
teste humano** → 8. **Documento de contexto** da task → 9. **Prompt de sincronia com o
frontend** → 10. **PR** (só depois do frontend + E2E verdes) → 11. Próxima task.

O passo 4 é um **gate**: não escreva código de implementação antes de o plano ser
revisado e as dúvidas resolvidas. Levante as dúvidas de uma vez, no plano, em vez de
gotejá-las durante a implementação.

#### Passos 6–7: quem testa o quê

A divisão é rígida e vale para toda task:

**Passo 6 — o agente testa tudo o que consegue testar.** Ao terminar a
implementação, verifique se a suíte **já cobre a mudança por inteiro**. Se cobrir e
passar, ótimo — diga isso. Se não cobrir, **escreva os testes**; não devolva ao
usuário nada que possa virar `pytest`. Além da suíte, exercite o que for verificável
por código: chamar o service com dado real (em transação com rollback), rodar o
comando, inspecionar o container, medir com o modelo de verdade. **Se o agente
consegue verificar, o agente verifica** — mandar o usuário conferir o que um teste
provaria é empurrar trabalho.

**Passo 7 — o roteiro de teste humano cobre SÓ o que exige um humano.** Entregue-o
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

O passo 8 existe porque **o contexto é zerado entre tasks**: cada uma fecha com
`docs/tasks/contexto-<task>.md` registrando o que era para fazer, o que foi feito,
as decisões de desenho, os bugs encontrados e como foram corrigidos, e o estado em
que a próxima task começa. Índice em `docs/tasks/README.md`.

Ainda no passo 8, atualize **"Estado atual"** logo abaixo e o status no
`ROADMAP.md`. São 3 linhas e evitam o pior modo de falha deste projeto: um agente
sem contexto reimplementando o que já existe, ou supondo que existe o que não
existe.

#### Passos 9–10: o frontend é o terceiro gate do PR

**O backend não vive sozinho.** O frontend é um repo **vizinho**
(`../../Frontend/Planejamento_Frontend/`, SPA React+Vite) que consome esta API por
`VITE_API_URL`. Um PR de backend só fecha quando os dois lados estão verdes juntos —
senão o `main` do backend passa a servir um contrato que o frontend ainda não fala.

**Passo 9 — prompt de sincronia (depois dos testes do backend, nunca antes).**
Assim que a suíte do backend passar, produza um **prompt para o usuário mandar ao
agente do frontend**. O prompt deve:

- dizer que o frontend está no **repositório vizinho**
  (`../../Frontend/Planejamento_Frontend/` a partir daqui);
- descrever as mudanças de contrato de forma **concreta e acionável**: endpoints
  novos/alterados, formas de request/response, parâmetros, códigos de status;
- **ou**, quando o PR não muda o contrato externo (foi o caso do PR1), dizer isso
  **explicitamente e com o porquê** — o prompt de "nada a fazer, eis a razão" é um
  artefato válido: dá ao agente do frontend como confirmar que nada quebrou.

O prompt é para o **usuário repassar**, não para o agente do backend executar: o
agente do backend não toca no repo do frontend.

**Passo 10 — o PR é gated.** **Não abra o PR** logo após os testes do backend. O PR
só pode ser aberto **depois** que o usuário aplicar as mudanças no frontend, rodar os
**testes end-to-end** lá e confirmar que está tudo verde. Até essa confirmação chegar,
o trabalho fica em espera — mesma disciplina do gate do passo 4: não avance sobre uma
premissa (aqui, "o frontend acompanhou") que ainda não foi confirmada.

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

*Atualizado em 24/07/2026 (fim do PR1 da Fase 0B). **Mantenha esta seção viva:**
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
| **1.1 / PR C** ✅ | **texto livre → knobs**: a IA lê a `descricao`, o guarda-corpo valida os knobs novos, e o plano devolve `leitura` (o que foi entendido — sempre reportado) e `perguntas` (até 3, priorizadas). **Vocabulário voltado ao usuário vem de tabela em `services/vocabulario.py`**, não de paráfrase do modelo, com teste que barra UUID/nome de campo. ⚠️ o 7b local **não** exercita isto (ver `contexto-fase1-prc.md` §4) | `services/vocabulario.py`, `services/planejamento_ia.py`, `tasks.py` |
| **1.1 / PR B** ✅ | **`aplicar_plano`**: o par que faltava do `simular_plano` — recalcula com os mesmos argumentos curtos (inclusive `a_partir_de`) e persiste. O plano **não** trafega pelo modelo; segunda chamada não duplica (esbarra em "já promovida"). Espelhado no MCP | `services/agente.py`, `mcp_server/server.py` |

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
- **Quem LIGA os parâmetros de agendamento.** Os campos existem e o solver os obedece
  (PR A, abaixo), mas **nada os preenche sozinho**: não há default de classe (decisão
  D2), o frontend não os mostra e a IA ainda não os emite. Hoje quem seta é a API, o
  admin ou `manage.py marcar_estrategia`. A IA passa a preencher no **PR C**, e
  `criar_tarefa` (ferramenta do agente) **ainda não aceita** esses knobs.
- **Endpoint para RESPONDER uma pergunta.** O plano devolve `perguntas`, mas aceitar
  uma é `PATCH /tarefas/{id}/` com o knob — funciona, não é caminho desenhado.
- **Cenários e refino não conhecem os knobs novos.** `cenarios.py` segue intocado.
- **Um modelo que use a camada de texto livre.** O mecanismo do PR C está pronto e
  testado, mas o `qwen2.5:7b` ignora a instrução de traduzir a `descricao` — medido
  duas vezes em 15/08/2026. Enquanto `LLM_PROVIDER=ollama` com o 7b, a camada fica
  ociosa (nada quebra: `leitura` reporta `entendi: []`).
- **Hospedagem.** Roda só local, via compose.

**Suíte:** 422 testes, dos quais 41 de isolamento (`planner/tests/test_isolamento.py`)
— os únicos que provam isolamento, porque usam **dois** perfis; o resto roda com um
só, onde "global" e "do dono" coincidem.

## Layout

Este diretório (`Planejamento_Backend/`, onde mora este arquivo) é a raiz do
projeto Django — `manage.py`, `docker-compose.yml` e `pyproject.toml` ficam aqui;
rode os comandos a partir daqui. O código de aplicação vive em `planner/`, config
em `config/`. (O repositório está aninhado em `.../planejamento/backend/`, então a
sessão pode abrir um nível acima.)

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
- **Ocorrências de eventos recorrentes são virtuais**: só existe linha `Ocorrencia`
  quando o usuário toca aquela data (conclui/remarca/pula). Expansão sob demanda.
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
  `EventoViewSet.list` e pelo planejador.
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
  (Marcos C1b/C5). Tem 2 dos 3 pontos que ainda chamam `ollama.Client` direto.
- `adaptacao.py` — aprende `PesoPreferencia` por **escolha revelada** (EWMA). Os pesos
  **ordenam e sugerem** cenários, nunca filtram.
- `replanejamento.py` — replaneja do agora em diante; simula (plano + diff) ou aplica,
  substituindo as sessões futuras.
- `agente.py` — agente conversacional com **tool use multi-turno** (Marco C7). Já tem a
  abstração de provider (`_OllamaProvider` / Anthropic + factory por `AGENTE_PROVIDER`)
  que a Fase 0A.1 vai estender ao resto. As ferramentas chamam os **services em
  processo** (PR0 da Fase 0B) — antes era `requests` contra a própria API. Cada
  ferramenta recebe `dono` como primeiro **posicional** e o dispatch o passa por fora
  do `**tc.args`: o que o modelo escolhe e a identidade de quem conversa chegam por
  caminhos diferentes, então nenhuma saída do LLM troca o dono dos dados.
- `tarefas.py` — `promover`/`planejar`/`criar` (regra que morava dentro das views).
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
