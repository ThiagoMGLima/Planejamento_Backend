"""URLs do app planner (sob /api/v1/) — Handoff §8.

Marco 2: DefaultRouter com classes/tarefas/eventos + health. A janela de
eventos, concluir/remarcar, pendentes e feriados entram no Marco 3.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"classes", views.ClasseViewSet, basename="classe")
router.register(r"tarefas", views.TarefaViewSet, basename="tarefa")
router.register(r"eventos", views.EventoViewSet, basename="evento")

urlpatterns = [
    path("health", views.health, name="health"),
    path("", include(router.urls)),
]
