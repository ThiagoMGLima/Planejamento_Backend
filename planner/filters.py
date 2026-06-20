"""Filtros de query (Handoff §8).

Marco 2: filtro de `status` no Inbox (GET /tarefas?status=INBOX). O filtro de
janela de eventos (inicio/fim) com expansão de ocorrências entra no Marco 3.
"""
from django_filters import rest_framework as filters

from .models import Tarefa


class TarefaFilter(filters.FilterSet):
    class Meta:
        model = Tarefa
        fields = ["status", "classe"]
