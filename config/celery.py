"""App Celery do planejador.

O worker sobe no docker-compose e executa `planner.tasks.planejar_ia_task` — o
job assíncrono do planejamento assistido por IA (Fase A). Tasks são descobertas
via autodiscover em cada app instalado (planner/tasks.py).
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("planejador")

# Lê config do settings do Django usando o prefixo CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks em cada app instalado (planner/tasks.py).
app.autodiscover_tasks()
