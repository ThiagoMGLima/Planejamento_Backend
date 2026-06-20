# Planejador de Rotina — Backend

API REST em Django/DRF para o Planejador de Rotina. **Projeto pessoal, 100%
local e single-user** — roda na máquina do dono via Docker, sem deploy em nuvem
e **sem autenticação** (acesso só em `localhost`).

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

## Rodando localmente

Requer Docker.

```bash
cp .env.example .env        # ajuste SECRET_KEY se quiser
docker compose up --build   # sobe db, redis, web e celery
```

O entrypoint do `web` aguarda o Postgres, aplica `migrate` (criando as 5 classes
padrão) e roda `collectstatic`.

- Health: <http://localhost:8000/api/v1/health> → `{"status": "ok"}`
- Admin: <http://localhost:8000/admin/> (crie um superuser para entrar)

```bash
# criar superuser para o admin
docker compose exec web python manage.py createsuperuser

# conferir as 5 classes padrão
docker compose exec web python manage.py shell -c \
  "from planner.models import Classe; print(list(Classe.objects.values_list('nome', flat=True)))"
```

## Endpoints (base `/api/v1/`)

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/health` | Healthcheck → 200 |
| GET/POST/PATCH/DELETE | `/classes/` | CRUD de classes (DELETE em uso → 409) |
| GET/POST/PATCH/DELETE | `/tarefas/` | CRUD de tarefas (Inbox); `?status=INBOX` |
| POST | `/tarefas/{id}/promover/` | Inbox → calendário (cria Evento) |
| GET | `/eventos/?inicio&fim` | Janela com ocorrências expandidas (≤ ~92 dias) |
| POST/PATCH/DELETE | `/eventos/` `/eventos/{id}/` | CRUD de eventos |
| POST | `/eventos/{id}/concluir/` `…/remarcar/` | Transições; `?escopo=ocorrencia\|serie` |
| GET | `/pendentes` | Eventos rastreáveis com `status_efetivo == PENDENTE` |
| GET | `/feriados?ano=2026` | Feriados nacionais (BrasilAPI, cacheado) |

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

Django 5.0 · DRF 3.15 · PostgreSQL 16 · Redis 7 · Celery 5.4 (provisionado, sem
jobs no MVP) · gunicorn · django-environ. Testes: pytest-django + factory_boy.
Lint/format: ruff + black. Versões fixadas em `requirements.txt` /
`requirements-dev.txt`.

## Variáveis de ambiente

Ver `.env.example`. Principais: `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`,
`ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`.
