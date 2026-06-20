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

**Marco 1 — Fundação, models e admin** ✅
Estrutura do projeto, 5 models (Classe, Tarefa, Evento, RegraRecorrencia,
Ocorrencia), migrations + seed das 5 classes padrão, Django admin, Docker
(Postgres + Redis + web + celery) e `GET /api/v1/health`.

Marcos 2–4 (serializers/CRUD, recorrência/feriados, testes/CI) ainda por vir.

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

## Stack

Django 5.0 · DRF 3.15 · PostgreSQL 16 · Redis 7 · Celery 5.4 (provisionado, sem
jobs no MVP) · gunicorn · django-environ. Versões fixadas em `requirements.txt`.

## Variáveis de ambiente

Ver `.env.example`. Principais: `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`,
`ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`.
