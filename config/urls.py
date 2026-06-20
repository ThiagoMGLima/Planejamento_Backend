"""URLs raiz do projeto.

A API fica sob /api/v1/ (Handoff §8). No Marco 1 só existe o health check;
os recursos REST (classes, tarefas, eventos) entram no Marco 2.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("planner.urls")),
]
