# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Backend Django/DRF do **Planejador de Rotina** — projeto pessoal, **100% local e
single-user**, roda via Docker, **sem autenticação** (acesso só em `localhost`).
Desvio deliberado do handoff: os models NÃO têm FK `dono`; constraints que seriam
por-dono são globais. Frontend é um repo separado (SPA Vite):
<https://github.com/ThiagoMGLima/Planejador_Frontend>.

Convenção de trabalho: **1 marco = 1 PR**. Fonte da verdade do contrato:
`Planejamento_Backend/Handoff de Backend - MVP.html`; plano: `PLAN.md`; notas de
design em `Planejamento_Backend/docs/tasks/`.

## Layout

Este diretório (`Planejamento_Backend/`, onde mora este arquivo) é a raiz do
projeto Django — `manage.py`, `docker-compose.yml` e `pyproject.toml` ficam aqui;
rode os comandos a partir daqui. O código de aplicação vive em `planner/`, config
em `config/`. (O repositório está aninhado em `.../planejamento/backend/`, então a
sessão pode abrir um nível acima.)

## Comandos

```bash
# subir tudo (db, redis, ollama, web, celery) — entrypoint do web faz migrate + collectstatic
docker compose up --build

# baixar o modelo da IA uma vez (a IA é opcional; ver IA abaixo)
docker compose exec ollama ollama pull qwen2.5:7b-instruct

# dados de exemplo (--clear zera tarefas/eventos antes; mantém as classes)
docker compose exec web python manage.py seed_demo --clear           # variado, com histórico
docker compose exec web python manage.py seed_planejamento --clear    # grande, futuro, p/ exercitar o planejador
```

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

### Models (`planner/models.py`)
`Classe`, `Tarefa` (Inbox), `Evento` (calendário), `RegraRecorrencia`,
`Ocorrencia`. Dois invariantes que atravessam o código:
- **`PENDENTE` é derivado na leitura, nunca gravado** (status efetivo calculado em
  `services/completion.py`).
- **Ocorrências de eventos recorrentes são virtuais**: só existe linha `Ocorrencia`
  quando o usuário toca aquela data (conclui/remarca/pula). Expansão sob demanda.

### Services
- `recurrence.py` — expande recorrência em ocorrências virtuais via `dateutil.rrule`,
  SEMPRE dentro de uma janela limitada (nunca série infinita). Reusado por
  `EventoViewSet.list` e pelo planejador.
- `completion.py` — deriva `PENDENTE`; `concluir`/`remarcar` são as únicas
  transições de escrita (remarcar devolve a `Tarefa` de origem ao Inbox).
- `holidays.py` — feriados via BrasilAPI no servidor, cache agressivo + cópia stale
  para sobreviver a falhas externas.
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

### Fluxo assíncrono do planejamento por IA
`POST /planejamento/planejar-ia` valida síncrono e enfileira `planejar_ia_task`
(`planner/tasks.py`, único job real) → responde 202 `{job_id}` (ou 200 se já em
cache). O front faz polling em `GET /planejamento/planejar-ia/{job_id}`. Resultado
é cacheado no Redis pela chave `(tarefa_ids + prefs efetivas + plano base)`. Se o
Ollama falhar ou `IA_PLANEJAMENTO_ENABLED=0`, degrada para o plano base do solver
com `ia_indisponivel: true`.

## Convenções da API

Rotas do router (`/classes/`, `/tarefas/`, `/eventos/`) exigem **barra final**;
`/classes/` e `/tarefas/` são paginadas por cursor, as demais retornam arrays. As
rotas avulsas (`/health`, `/pendentes`, `/feriados`, `/planejamento/*`) são
`path()` sem barra final.

## IA / Ollama

Roda em **CPU** por padrão (`qwen2.5:7b-instruct`), na casa de dezenas de segundos
por plano; o modelo fica residente (`OLLAMA_KEEP_ALIVE=-1`) para evitar cold start.
Variáveis: `IA_PLANEJAMENTO_ENABLED`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`,
`OLLAMA_TIMEOUT`; calibração da estimativa: `PLANEJAR_TEMPO_BASE_S`,
`PLANEJAR_TEMPO_POR_TAREFA_S`. Ver `.env.example`.
