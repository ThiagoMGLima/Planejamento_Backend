# Plano de produção — Backend do Planejador de Rotina

> **Como usar este arquivo.** Este é o plano aprovado para construir o backend.
> Junto com `Handoff de Backend - MVP.html` (o contrato de implementação), ele é
> autossuficiente: copie **os dois** para o repositório `planejador-backend` e, numa
> sessão do Claude Code apontada para esse repo, basta pedir
> *"Leia o Handoff de Backend e o PLAN.md e execute o Marco 1"*.

## Context

O Planejador de Rotina hoje tem apenas documentação (Especificação v1.1, Handoff de
Design e o **Handoff de Backend - MVP**). Não existe código de backend. Este plano
executa a **Fase 1** do backend conforme o handoff, que é a fonte da verdade de
implementação (modelo de dados, contrato REST, máquina de estados, recorrência,
feriados, timezone, auth, Docker).

Decisões que moldam o plano:
- **Repositório separado** `planejador-backend` (split deploy: backend Railway/EC2,
  frontend Vercel).
- **Por etapas** — 4 marcos, cada um em seu PR, para revisão incremental.
- **JWT desde já** (multiusuário, escopo por dono).
- **Celery + Redis provisionados desde já** (terreno pronto, mesmo sem jobs reais).

Resultado pretendido: uma API REST Django executável em Docker, cobrindo o checklist
da §15 do handoff, pronta para o frontend consumir.

---

## Passo 0 — Repositório

- Repo `planejador-backend` (privado), com README e branch `main`.
- Cada marco = uma branch + um PR contra `main`.

---

## Marco 1 — Fundação, models e admin  (PR 1)

**Estrutura** (handoff §3): projeto `config/`, app `planner/` com `services/`.

- `requirements.txt` com versões fixadas do handoff §2 (Django 5.0, DRF 3.15,
  psycopg[binary] 3, python-dateutil, django-filter, simplejwt, django-cors-headers,
  requests, celery+redis, gunicorn, django-environ).
- `config/settings.py` via `django-environ`: `DATABASE_URL` (Postgres), `USE_TZ=True`,
  `TIME_ZONE="America/Sao_Paulo"`, DRF defaults, cache Redis (`REDIS_URL`), CORS,
  config simplejwt. `config/celery.py` + integração em `config/__init__.py`.
- **Models** (handoff §4) em `planner/models.py`, todos com `id` UUID, `dono` FK→User,
  `criado_em`/`atualizado_em`:
  - `Classe` — `nome`, `cor` (hex), `rastreia_conclusao`; unique `(dono, nome)`.
  - `Tarefa` — `titulo`, `descricao`, `classe?`, `deadline?`, `esforco_estimado?`,
    `status` (INBOX|PROMOVIDA). Campos Fase 2 no schema, sem lógica.
  - `Evento` — `titulo`, `inicio`, `fim`, `classe` (PROTECT), `rastrear_conclusao`,
    `status` (AGENDADO|CONCLUIDO|REMARCADO, nullable), `origem_tarefa?`,
    `regra_recorrencia?`. Index `(dono, inicio, fim)`; CheckConstraint `fim > inicio`.
  - `RegraRecorrencia` — `tipo` (SEMANAL|MENSAL), `dias` (ArrayField), `ignorar_feriados`,
    `data_fim?`.
  - `Ocorrencia` — `evento` FK, `data`, `inicio_override?`, `fim_override?`,
    `status_override?`; unique `(evento, data)`.
- Migrations iniciais. Registro no Django admin (apoio a debug/seed).
- **Docker**: `Dockerfile` (gunicorn), `docker-compose.yml` com serviços `db` (postgres:16),
  `redis`, `web`, `celery` (worker), volumes; `.env.example` (handoff §13).
  Entrypoint roda `migrate` + `collectstatic`.
- Endpoint `GET /api/v1/health` → 200.

**Critérios:** `docker compose up` sobe; `migrate` aplica; admin acessível; health 200.

---

## Marco 2 — Auth, serializers e CRUD  (PR 2)

- **Auth JWT** (handoff §11): `POST /api/v1/auth/token` + `/refresh` (simplejwt).
  Permissões `IsAuthenticated` global + `IsOwner` por objeto. Todo queryset filtra
  `dono=request.user` (defesa em profundidade).
- **Seed de classes padrão** via signal `post_save` de `User`: Aula, Tarefas básicas,
  Estudar, Prova, Trabalho com cores/rastreamento da handoff §4.1.
- **Serializers** (handoff §9) em `planner/serializers.py`:
  - `EventoSerializer`: `classe` aninhada (leitura) + `classe_id` (escrita); default de
    `rastrear_conclusao` herdado da classe; coerção de `status` (false→null;
    true sem status→AGENDADO); `status_efetivo` como `SerializerMethodField` read-only
    (stub neste marco, lógica real no Marco 3).
  - Validações: `fim > inicio`; cor regex `^#[0-9a-fA-F]{6}$`; `dias` por tipo
    (SEMANAL 0–6, MENSAL 1–31, não vazio).
- **ViewSets + DefaultRouter** (handoff §8) em `planner/views.py` + `planner/urls.py`:
  `classes`, `tarefas`, `eventos` (CRUD). DELETE de classe em uso → 409 (PROTECT).
- Ação custom `POST /api/v1/tarefas/{id}/promover` (arrasto Inbox→calendário): cria
  Evento herdando classe e `rastrear_conclusao`, liga `origem_tarefa`, marca Tarefa
  PROMOVIDA; `fim` = `esforco_estimado` ou 1h se ausente. `@transaction.atomic`.

**Critérios:** obter token; CRUD isolado por dono; 409 ao apagar classe em uso;
validação `fim>inicio`; promover herda classe corretamente.

---

## Marco 3 — Recorrência, feriados e pendência/transições  (PR 3)

- `planner/services/completion.py`:
  - `status_efetivo(evento|ocorrencia, agora=None)` — deriva PENDENTE
    (`rastrear` + `agora>fim` + ainda AGENDADO); respeita CONCLUIDO/REMARCADO e
    `status_override`/`fim_override` em ocorrências (handoff §5.1). Plugar no serializer.
  - Transições `concluir` / `remarcar` (atômicas, handoff §5.2): remarcar grava
    REMARCADO e **reabre/recria a Tarefa de origem como INBOX**.
- `planner/services/recurrence.py`: `expandir(evento, janela_inicio, janela_fim, feriados)`
  via `dateutil.rrule` (WEEKLY/MONTHLY), `montar_ocorrencia` aplicando overrides
  persistidos (inclui "PULADO"). Sempre limitado à janela; nunca série infinita (§6).
- `planner/services/holidays.py`: `feriados_do_ano(ano)` consumindo BrasilAPI
  `feriados/v1/{ano}` com cache (30 dias) e degradação graciosa em falha (§7).
- **Endpoints** (handoff §8): `GET /api/v1/eventos?inicio&fim` expande ocorrências e
  devolve `status_efetivo` (rejeita janela aberta / > ~92 dias, 400);
  `POST /eventos/{id}/concluir`, `POST /eventos/{id}/remarcar` com
  `?escopo=ocorrencia|serie`; `GET /api/v1/pendentes` (rastreáveis com PENDENTE, ordem
  por `fim` asc); `GET /api/v1/feriados?ano=`.

**Critérios:** expansão semanal/mensal correta com ignorar_feriados e data_fim; override
isolado não afeta série; remarcar devolve ao Inbox; pendentes calculado, nunca gravado.

---

## Marco 4 — Testes, CI e finalização  (PR 4)

- `pytest-django` + `factory_boy`. Unit em `services/`: fronteiras de `status_efetivo`;
  expansão de recorrência (semanal/mensal, feriados, data_fim, override); transação de
  remarcar. Contrato de API: escopo por dono, 409 classe-em-uso, `fim>inicio`, rejeição
  de janela aberta, herança em promover (handoff §14).
- Lint/format `ruff` + `black`; checagem de migrations pendentes.
- GitHub Actions CI (lint + testes + migration check) com serviço Postgres.
- `README.md` do backend (setup, env, comandos) apontando o handoff como contrato.

**Critérios:** suíte verde; CI passando; checklist da handoff §15 completo.

---

## Critical files (no repo planejador-backend)

- `config/settings.py`, `config/urls.py`, `config/celery.py`
- `planner/models.py`, `planner/serializers.py`, `planner/views.py`, `planner/urls.py`,
  `planner/filters.py`
- `planner/services/{completion,recurrence,holidays}.py`
- `Dockerfile`, `docker-compose.yml`, `.env.example`, `requirements.txt`
- `planner/tests/`, `.github/workflows/ci.yml`

Reaproveitar integralmente o contrato do `Handoff de Backend - MVP.html` — ele já contém
models, assinaturas dos serviços, payloads e endpoints de referência.

---

## Verificação (end-to-end)

1. `cp .env.example .env` e ajustar segredos; `docker compose up --build`.
2. `docker compose exec web python manage.py migrate` + criar superuser.
3. `POST /api/v1/auth/token` → obter Bearer; confirmar seed de 5 classes.
4. Fluxo: criar Tarefa → `promover` → `GET /eventos?inicio&fim` mostra o bloco com
   `status_efetivo`; recorrente semanal aparece expandido na janela; `concluir` e
   `remarcar` (remarcar reabre Tarefa no Inbox); `GET /pendentes` lista vencidos;
   `GET /feriados?ano=2026` retorna datas (e degrada bem se a BrasilAPI cair).
5. `docker compose exec web pytest` verde; CI verde no PR.
