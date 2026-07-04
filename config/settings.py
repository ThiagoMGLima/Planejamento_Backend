"""Configurações Django do Planejador de Rotina — backend (Fase 1 / Marco 1).

Baseado no Handoff de Backend (§10, §11, §13). Desvios deliberados por ser um
projeto pessoal, local e single-user (ver PLAN.md):
  - SEM autenticação: DRF roda com AllowAny (acesso só em localhost).
  - SEM FK `dono` nos models, SEM filtro de queryset por dono.
Config por variáveis de ambiente via django-environ.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "0.0.0.0"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:3000", "http://localhost:5173"]),
)

# Lê um arquivo .env se existir (em dev). Em Docker, as vars vêm do env_file.
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

# --- Núcleo --------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceiros
    "rest_framework",
    "django_filters",
    "corsheaders",
    # Apps do projeto
    "planner",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Banco de dados ------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://planejador:dev@db:5432/planejador",
    ),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Validação de senha (mantida p/ o admin) -----------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internacionalização e timezone (Handoff §10) ------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# --- Arquivos estáticos --------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Cache (Redis; locmem como fallback em dev) — Handoff §7 -------------
REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

# --- Django REST Framework (Handoff §8) ----------------------------------
# Desvio local/single-user: API aberta (AllowAny), sem auth.
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": "planner.pagination.CriadoEmCursorPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# --- CORS (origem local do frontend) — Handoff §13 -----------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

# --- Celery — Handoff §13 -------------------------------------------------
# Broker/backend configuráveis por env: a CI roda SEM Redis (memory:// e
# cache+memory://) — senão os testes eager que armazenam resultado tentariam
# conectar em redis://redis e falhariam. No compose o default cai no serviço
# redis, como sempre.
CELERY_BROKER_URL = env(
    "CELERY_BROKER_URL", default=REDIS_URL or "redis://redis:6379/0"
)
CELERY_RESULT_BACKEND = env(
    "CELERY_RESULT_BACKEND", default=REDIS_URL or "redis://redis:6379/0"
)
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

# --- Planejamento assistido por IA (Ollama local — Fase A) ---------------
# IA é opcional: se o Ollama falhar/estiver desligado (ou ENABLED=0), a task
# entrega o plano base do solver + flag `ia_indisponivel`.
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://ollama:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", default="qwen2.5:7b-instruct")
# 300s: o 7B no CPU é lento para contextos grandes (muitas tarefas → muitas
# diretrizes geradas). Com o modelo já residente (OLLAMA_KEEP_ALIVE=-1 no compose)
# não há cold start; este teto cobre a geração de planos grandes. O front espera
# um pouco mais que isso (ver planejarComIA em api.js).
OLLAMA_TIMEOUT = env.int("OLLAMA_TIMEOUT", default=300)
IA_PLANEJAMENTO_ENABLED = env.bool("IA_PLANEJAMENTO_ENABLED", default=True)

# Estimativa de tempo mostrada antes de gerar (endpoint planejar-ia/estimativa).
# Modelo linear base + por-tarefa: o tempo é dominado pela base (modelo warm no
# CPU ~53s) e cresce com o nº de tarefas no escopo, não com o horizonte em si.
PLANEJAR_TEMPO_BASE_S = env.int("PLANEJAR_TEMPO_BASE_S", default=55)
PLANEJAR_TEMPO_POR_TAREFA_S = env.int("PLANEJAR_TEMPO_POR_TAREFA_S", default=3)

# --- Agente conversacional (Marco C4, o "cérebro") ----------------------
# O framework do modelo é TROCÁVEL (visão §5): `ollama` (local, mesma infra da
# Fase A; fraco para agência multi-turno) ou `anthropic` (API remota, o que a
# visão recomenda para tool use multi-turno). Solver, dados e ferramentas
# permanecem locais em qualquer caso.
AGENTE_ENABLED = env.bool("AGENTE_ENABLED", default=True)
AGENTE_PROVIDER = env("AGENTE_PROVIDER", default="ollama")  # ollama | anthropic
AGENTE_MODEL = env("AGENTE_MODEL", default="claude-opus-4-8")  # usado se anthropic
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
# URL da PRÓPRIA API local: as ferramentas do agente batem nela (mesmos contratos
# HTTP que o MCP server embrulha). No worker Celery precisa alcançar o web
# (no compose: http://web:8000/api/v1).
API_BASE_URL = env("API_BASE_URL", default="http://localhost:8000/api/v1")
