# Planejador de Rotina — Backend

API REST em Django/DRF para o Planejador de Rotina. Roda via Docker, sem deploy
em nuvem e **ainda sem autenticação** (acesso só em `localhost`) — mas o schema e
todo o caminho de dados **já são multi-tenant**: existe `Perfil`, os models-raiz
têm FK `dono` obrigatória e as consultas são escopadas (Fase 0B / PR1).

- **Frontend (SPA Vite):** <https://github.com/ThiagoMGLima/Planejador_Frontend>
  — consome esta API; configure a origem dele em `CORS_ALLOWED_ORIGINS`.
- **Contrato de implementação:** `Handoff de Backend - MVP.html` (fonte da verdade).
- **Plano de execução:** `PLAN.md` (4 marcos, um PR cada).
- **Mapa de produto:** `ROADMAP.md` — a evolução de projeto pessoal para produto,
  com o método de trabalho por task.
- **Desvios do handoff que ainda valem:** sem JWT, sem endpoints `/auth/*`, sem
  `IsAuthenticated`/`IsOwner` — a API segue aberta em `localhost`.
  > **`dono` e filtro por dono deixaram de ser desvio** (Fase 0B / PR1): os 8
  > models-raiz têm `dono` obrigatório, a unicidade é por-dono e o manager
  > **recusa consulta sem escopo**. Falta só quem diz *quem é o usuário* — é o
  > PR2 (Supabase Auth). Ver `docs/tasks/contexto-0b-pr1.md`.

## Status

MVP (Fase 1) completo — 4 marcos:

- **Marco 1 — Fundação, models e admin** ✅ estrutura, 5 models, migrations +
  seed das classes padrão, admin, Docker, `GET /api/v1/health`.
- **Marco 2 — Serializers e CRUD** ✅ CRUD de classes/tarefas/eventos, validações
  (§9), `POST /tarefas/{id}/promover`, 409 ao apagar classe em uso.
- **Marco 3 — Recorrência, feriados e pendência** ✅ expansão via `rrule`,
  BrasilAPI com cache, `status_efetivo` derivado, `concluir`/`remarcar`,
  `GET /eventos?inicio&fim`, `/pendentes`, `/feriados`.
- **Marco 4 — Testes, CI e finalização** ✅ pytest + factory_boy, ruff + black,
  GitHub Actions (lint + checagem de migrations + testes com Postgres).

**Fase A — Planejamento (solver + IA)** ✅ planejador de produção multitarefa
(solver EDF guloso, com cascata de relaxamento) e camada de IA opcional via
Ollama local que aperfeiçoa o plano. Ver abaixo.

**Fase C — Rotina inteligente** ✅ (visão em `docs/tasks/visao-rotina-inteligente.md`)

- **C1a/C1b** — vocabulário do solver e pipeline de **cenários** com trade-offs;
  aprendizado de `PesoPreferencia` por escolha revelada (EWMA).
- **C2** — **replanejar** do agora em diante (simular com diff, ou aplicar).
- **C3** — `RegistroExecucao` alimentando os fatores adaptativos.
- **C4/C7** — **servidor MCP** + **agente conversacional** com tool use.
- **C5** — refino conversacional de cenários.
- **C6** — estimativa adaptativa da duração dos jobs de IA.
- **C8** — feriados regionais (estadual por UF, offline + municipal manual).

**Fase 0B — Contas** 🔜 em andamento (4 PRs):

- **PR0** ✅ views finas + ferramentas do agente chamando os services em processo.
- **PR1** ✅ `Perfil`, FK `dono` nos 8 models-raiz, unicidade por-dono e o manager
  que exige escopo; 41 testes de isolamento com dois perfis.
- **PR2** 🔜 `SupabaseJWTAuthentication` + provisionamento JIT — **bloqueado**: exige
  o projeto Supabase criado.
- **PR3** ⏳ conta demo + gate de pagamento stub.

**Próximo:** PR2. Contexto em `docs/tasks/README.md`.

## Planejamento (solver + IA)

O cliente seleciona tarefas (precisam de **deadline + esforço + classe**) e pede
um plano de sessões de produção:

1. **Solver** (`POST /planejamento/calcular`, síncrono): aloca as tarefas em
   sessões respeitando janelas, tetos diários e eventos já no calendário
   ("ocupado"). O que não couber volta em `nao_alocado`.
2. **IA** (`POST /planejamento/planejar-ia`, assíncrono via Celery): roda o
   solver, manda os FATOS para o modelo, que devolve **diretrizes** (prioridades,
   buffers, tetos diários por tarefa e total) buscando uma rotina mais **humana**
   — distribuir o esforço, suavizar picos de carga e deixar folga antes dos
   prazos — e re-roda o solver. Degrada para o plano base se a IA estiver
   indisponível/desligada (`ia_indisponivel: true`).
3. **Aplicar** (`POST /planejamento/aplicar`): cria os eventos-sessão a partir do
   plano revisado pelo usuário.

O **horizonte** do plano é escolhível (`AUTOMATICO` | `SEMANA` | `DUAS_SEMANAS`
| `MES`); quanto maior, mais tarefas entram no escopo e mais a IA "pensa" — daí o
endpoint de **estimativa** de tempo antes de gerar. O compose roda o Ollama na
**GPU AMD via ROCm** (`qwen2.5:7b-instruct`); em máquina sem `/dev/kfd` cai para
CPU pelo override abaixo, na casa de dezenas de segundos por plano.

## Rodando localmente

Requer Docker.

```bash
cp .env.example .env        # ajuste SECRET_KEY se quiser
docker compose up --build   # sobe db, redis, ollama, web, celery e mcp
```

O entrypoint do `web` aguarda o Postgres, aplica `migrate` (criando as 5 classes
padrão) e roda `collectstatic`.

> **Máquina sem GPU AMD.** O serviço `ollama` está fixado em ROCm para a RX 7600
> (`/dev/kfd`, `group_add: 990`). Onde não existe `/dev/kfd` ele não sobe — e como o
> `web` depende dele, a stack trava. Crie um `docker-compose.override.yml` local
> (deixe-o fora do git) para cair na imagem de CPU:
>
> ```yaml
> services:
>   ollama:
>     image: ollama/ollama
>     devices: !reset []
>     group_add: !reset []
>     environment: !override
>       OLLAMA_KEEP_ALIVE: "-1"
> ```
>
> O `!reset` é necessário: listas em override são **concatenadas**, não substituídas.
> Em CPU, considere `OLLAMA_MODEL=qwen2.5:3b-instruct` (~1.9 GB) no lugar do 7b.

Para usar a IA, baixe o modelo uma vez (a IA é opcional — desligue com
`IA_PLANEJAMENTO_ENABLED=0` para entregar só o plano base do solver):

```bash
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

- Health: <http://localhost:8000/api/v1/health> → `{"status": "ok"}`
- Admin: <http://localhost:8000/admin/> (crie um superuser para entrar)

```bash
# criar superuser para o admin
docker compose exec web python manage.py createsuperuser

# conferir as 5 classes padrão do perfil local
# (`Classe.objects.values_list(...)` sozinho levanta EscopoAusente — ver "Escopo por dono")
docker compose exec web python manage.py shell -c \
  "from planner.services.perfis import perfil_local; from planner.models import Classe; \
   print(list(Classe.objects.do_dono(perfil_local()).values_list('nome', flat=True)))"

# popular dados de exemplo (--clear zera tarefas/eventos antes; mantém classes)
docker compose exec web python manage.py seed_demo --clear           # dataset variado, com histórico
docker compose exec web python manage.py seed_planejamento --clear    # dataset grande, futuro, p/ exercitar o planejador
```

Os dois seeds **não convivem** — `--clear` zera tarefas e eventos, então rodar um
substitui o dataset do outro. Sem `--clear` eles **acumulam** (não são idempotentes).
Ambos aceitam `--dono <email>` e escrevem **num perfil só** (default: o local); o
`--clear` também respeita esse escopo e nunca toca em dados de outra conta.

## Escopo por dono

Desde a Fase 0B / PR1, o manager dos models-raiz **recusa consulta sem escopo** —
isolamento entre contas é o default, não uma convenção a lembrar:

```python
Evento.objects.do_dono(perfil).filter(...)   # o caminho normal
Evento.objects.sem_escopo().filter(...)      # varredura global — só seeds e admin
Evento.objects.all()                         # levanta EscopoAusente
```

Vale para `shell`, scripts e qualquer código novo; os related managers reversos
(`tarefa.eventos.all()`) herdam a guarda. Enquanto não há login,
`services/perfis.perfil_do_request()` devolve sempre o **perfil local** — é a
única função que o PR2 vai trocar. Detalhe em `CLAUDE.md`, seção "Escopo por dono".

## Endpoints (base `/api/v1/`)

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/health` | Healthcheck → 200 |
| GET/POST/PATCH/DELETE | `/classes/` | CRUD de classes (DELETE em uso → 409) |
| GET/POST/PATCH/DELETE | `/tarefas/` | CRUD de tarefas (Inbox); `?status=INBOX` |
| POST | `/tarefas/{id}/promover/` | Inbox → calendário (cria Evento) |
| POST | `/tarefas/{id}/planejar/` | Divide a produção de uma tarefa em N eventos-sessão |
| GET | `/eventos/?inicio&fim` | Janela com ocorrências expandidas (≤ ~92 dias) |
| POST/PATCH/DELETE | `/eventos/` `/eventos/{id}/` | CRUD de eventos |
| POST | `/eventos/{id}/concluir/` `…/remarcar/` | Transições; `?escopo=ocorrencia\|serie` |
| GET | `/pendentes` | Eventos rastreáveis com `status_efetivo == PENDENTE` |
| GET | `/feriados?ano=2026` | Feriados: nacional (BrasilAPI, cacheado) ∪ estadual (`FERIADOS_UF`) ∪ municipal (do perfil) |
| POST | `/planejamento/calcular` | Preview do plano pelo solver (síncrono, não persiste) |
| POST | `/planejamento/planejar-ia` | Plano aperfeiçoado pela IA → 202 `{job_id}` (ou 200 se em cache) |
| GET | `/planejamento/planejar-ia/estimativa` | Tempo estimado da geração, antes de disparar |
| GET | `/planejamento/planejar-ia/{job_id}` | Estado/resultado do job assíncrono |
| POST | `/planejamento/aplicar` | Cria os eventos-sessão a partir do plano revisado |
| POST | `/planejamento/cenarios` | 3–4 cenários com trade-offs → 202 `{job_id}` (ou 200 se em cache) |
| GET | `/planejamento/cenarios/{job_id}` | Estado/resultado do job de cenários |
| POST | `/planejamento/cenarios/escolher` | Grava a escolha (aprende pesos); `aplicar=true` persiste o plano |
| POST | `/planejamento/cenarios/refinar` | Refino conversacional de um cenário → 202 `{job_id}` |
| GET | `/planejamento/cenarios/refinar/{job_id}` | Estado/resultado do job de refino |
| POST | `/planejamento/agente/chat` | Agente conversacional (tool use) → 202 `{job_id}` |
| GET | `/planejamento/agente/chat/{job_id}` | Estado/resultado do turno do agente |
| POST | `/planejamento/replanejar` | Replaneja do agora em diante (simulação: plano + diff) |
| POST | `/planejamento/replanejar/aplicar` | Recalcula e persiste (substitui as sessões futuras) |

Listas de `/classes/` e `/tarefas/` são paginadas por cursor (`{next, previous,
results}`); `/eventos/` e `/pendentes` retornam arrays; `/feriados` retorna um
**objeto** `{ano, feriados: [...]}`. Rotas do router exigem **barra no final**; as
avulsas (`/health`, `/pendentes`, `/feriados`, `/planejamento/*`) são **sem** barra.

A janela de `/eventos/?inicio&fim` exige datas **tz-aware** (com offset), ex.
`2026-07-20T00:00:00-03:00` — data nua devolve 400.

## Servidor MCP (agente conversacional)

O serviço `mcp` do compose expõe as ferramentas do backend via
**Model Context Protocol** (transporte streamable-http) em
`http://localhost:8765/mcp` — camada fina sobre a API, zero lógica própria.
Ferramentas (12): `criar_tarefa`, `listar_classes`, `listar_tarefas`,
`listar_pendentes`, `consultar_agenda`, `concluir`, `remarcar`, `simular_plano`
(what-if, não persiste), `gerar_cenarios` (encapsula o polling),
`refinar_cenario`, `escolher_cenario` e `replanejar` (simular/aplicar).

Qualquer cliente MCP serve como runtime do agente. Exemplo com Claude Code:

```bash
claude mcp add --transport http planejador http://localhost:8765/mcp
```

Realismo de hardware: o 7B/CPU local dá conta das chamadas únicas com schema
(diretrizes, cenários); agência multi-turno com tool use pede modelo maior —
o runtime do agente é externo e trocável (as variáveis `AGENTE_*` ficam fora
do core do backend). Solver, diretrizes e dados continuam 100% locais.

## Desenvolvimento e testes

```bash
# rodar a suíte e o lint via Docker (DB já no compose)
docker compose run --rm web sh -c "pip install -r requirements-dev.txt && pytest"

# fora do Docker (precisa de Postgres acessível + DATABASE_URL):
pip install -r requirements-dev.txt
ruff check .
black --check .
python manage.py makemigrations --check --dry-run
pytest
```

A CI (GitHub Actions, `.github/workflows/ci.yml`) roda ruff, black `--check`,
checagem de migrations pendentes e a suíte pytest contra um Postgres de serviço.

## Stack

Django 5.0 · DRF 3.15 · PostgreSQL 16 · Redis 7 · Celery 5.4 (job assíncrono do
planejamento por IA) · Ollama (`qwen2.5:7b-instruct`, local) · gunicorn ·
django-environ. Testes: pytest-django + factory_boy. Lint/format: ruff + black.
Versões fixadas em `requirements.txt` / `requirements-dev.txt`.

## Variáveis de ambiente

Ver `.env.example`. Principais: `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`,
`ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` (inclua a origem do frontend).

Planejamento por IA: `IA_PLANEJAMENTO_ENABLED` (1/0), `OLLAMA_BASE_URL`,
`OLLAMA_MODEL`, `OLLAMA_TIMEOUT`. Calibração da estimativa de tempo (opcionais,
com default): `PLANEJAR_TEMPO_BASE_S`, `PLANEJAR_TEMPO_POR_TAREFA_S`.

Agente conversacional: `AGENTE_ENABLED` (1/0), `AGENTE_PROVIDER`
(`ollama` local | `anthropic` remoto), `AGENTE_MODEL` e `ANTHROPIC_API_KEY`.
As ferramentas do agente chamam os services **em processo**, então não há
`API_BASE_URL` do lado do Django; a variável sobrou só para o servidor MCP, e o
`docker-compose.yml` já a define no serviço `mcp`.

Feriados regionais: `FERIADOS_UF` (camada estadual offline via lib `holidays`; vazio
desliga). Os municipais ficam no admin, em *Feriados locais*.
