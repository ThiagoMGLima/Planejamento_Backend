"""Garante que o app Celery seja carregado quando o Django iniciar.

Assim o decorator @shared_task usa o app configurado em config/celery.py.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
