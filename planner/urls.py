"""URLs do app planner (sob /api/v1/) — Handoff §8.

DefaultRouter com classes/tarefas/eventos (+ ações promover/concluir/remarcar)
e as rotas avulsas health, pendentes e feriados.
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
    path("pendentes", views.pendentes, name="pendentes"),
    path("feriados", views.feriados, name="feriados"),
    path("", include(router.urls)),
]
