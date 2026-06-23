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
    path(
        "planejamento/calcular",
        views.planejamento_calcular,
        name="planejamento-calcular",
    ),
    path(
        "planejamento/planejar-ia",
        views.planejamento_planejar_ia,
        name="planejar-ia",
    ),
    path(
        "planejamento/planejar-ia/estimativa",
        views.planejamento_estimativa,
        name="planejar-ia-estimativa",
    ),
    path(
        "planejamento/planejar-ia/<str:job_id>",
        views.planejamento_planejar_ia_status,
        name="planejar-ia-status",
    ),
    path(
        "planejamento/aplicar",
        views.planejamento_aplicar,
        name="planejamento-aplicar",
    ),
    path("notion/sync", views.notion_sincronizar, name="notion-sync"),
    path("", include(router.urls)),
]
