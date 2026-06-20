"""App Celery (provisionado desde já; sem jobs reais no MVP — Handoff §13).

O worker sobe no docker-compose, mas nenhuma task é definida na Fase 1.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("planejador")

# Lê config do settings do Django usando o prefixo CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks em cada app instalado (planner/tasks.py, quando existir).
app.autodiscover_tasks()
