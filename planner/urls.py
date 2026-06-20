"""URLs do app planner (sob /api/v1/).

Marco 1: somente health. O DefaultRouter com classes/tarefas/eventos entra no
Marco 2.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health, name="health"),
]
