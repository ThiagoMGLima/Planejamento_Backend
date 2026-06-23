# Planejador de Rotina — Backend

API REST em Django/DRF para o Planejador de Rotina. **Projeto pessoal, 100%
local e single-user** — roda na máquina do dono via Docker, sem deploy em nuvem
e **sem autenticação** (acesso só em `localhost`).

- **Frontend (SPA Vite):** <https://github.com/ThiagoMGLima/Planejador_Frontend>
  — consome esta API; configure a origem dele em `CORS_ALLOWED_ORIGINS`.
- **Contrato de implementação:** `Handoff de Backend - MVP.html` (fonte da verdade).
- **Plano de execução:** `PLAN.md` (4 marcos, um PR cada).
- **Desvios deliberados do handoff** (por ser local/single-user): sem JWT, sem
  endpoints `/auth/*`, sem `IsAuthenticated`/`IsOwner`, sem FK `dono` nos models
  e sem filtro de queryset por dono.

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
endpoint de **estimativa** de tempo antes de gerar. A IA roda em **CPU** por
padrão (`qwen2.5:7b-instruct`), na casa de dezenas de segundos por plano.

## Rodando localmente

Requer Docker.

```bash
cp .env.example .env        # ajuste SECRET_KEY se quiser
docker compose up --build   # sobe db, redis, ollama, web e celery
```

O entrypoint do `web` aguarda o Postgres, aplica `migrate` (criando as 5 classes
padrão) e roda `collectstatic`.

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

# conferir as 5 classes padrão
docker compose exec web python manage.py shell -c \
  "from planner.models import Classe; print(list(Classe.objects.values_list('nome', flat=True)))"

# popular dados de exemplo (--clear zera tarefas/eventos antes; mantém classes)
docker compose exec web python manage.py seed_demo --clear           # dataset variado, com histórico
docker compose exec web python manage.py seed_planejamento --clear    # dataset grande, futuro, p/ exercitar o planejador
```

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
| GET | `/feriados?ano=2026` | Feriados nacionais (BrasilAPI, cacheado) |
| POST | `/planejamento/calcular` | Preview do plano pelo solver (síncrono, não persiste) |
| POST | `/planejamento/planejar-ia` | Plano aperfeiçoado pela IA → 202 `{job_id}` (ou 200 se em cache) |
| GET | `/planejamento/planejar-ia/estimativa` | Tempo estimado da geração, antes de disparar |
| GET | `/planejamento/planejar-ia/{job_id}` | Estado/resultado do job assíncrono |
| POST | `/planejamento/aplicar` | Cria os eventos-sessão a partir do plano revisado |

Listas de `/classes/` e `/tarefas/` são paginadas por cursor (`{next, previous,
results}`); `/eventos`, `/pendentes` e `/feriados` retornam arrays. Rotas do
router exigem **barra no final**.

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
