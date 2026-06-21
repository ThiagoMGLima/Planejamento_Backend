#!/usr/bin/env bash
# Entrypoint do serviço web: aguarda o Postgres, aplica migrations (cria as 5
# classes padrão via data migration) e coleta estáticos antes de iniciar.
set -e

echo "Aguardando o banco de dados..."
python <<'PY'
import os
import time

import environ

env = environ.Env()
url = env.db("DATABASE_URL", default="postgres://planejador:dev@db:5432/planejador")

import psycopg

dsn = (
    f"host={url['HOST']} port={url['PORT']} dbname={url['NAME']} "
    f"user={url['USER']} password={url['PASSWORD']}"
)
for tentativa in range(30):
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Banco de dados indisponível após 30 tentativas.")
print("Banco disponível.")
PY

echo "Aplicando migrations..."
python manage.py migrate --noinput

echo "Coletando estáticos..."
python manage.py collectstatic --noinput

exec "$@"
