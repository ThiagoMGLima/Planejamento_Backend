"""Paginação por cursor (Handoff §8).

Todas as entidades herdam `criado_em` de TimestampedModel, então usamos esse
campo como ordenação estável do cursor.
"""
from rest_framework.pagination import CursorPagination


class CriadoEmCursorPagination(CursorPagination):
    ordering = "-criado_em"
    page_size = 100
