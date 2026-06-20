"""Registro no Django admin — apoio a debug/seed (Handoff §13, Marco 1)."""
from django.contrib import admin

from .models import Classe, Evento, Ocorrencia, RegraRecorrencia, Tarefa


@admin.register(Classe)
class ClasseAdmin(admin.ModelAdmin):
    list_display = ("nome", "cor", "rastreia_conclusao", "criado_em")
    search_fields = ("nome",)


@admin.register(Tarefa)
class TarefaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "classe", "status", "deadline", "criado_em")
    list_filter = ("status", "classe")
    search_fields = ("titulo",)
    autocomplete_fields = ("classe",)


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "classe", "inicio", "fim", "rastrear_conclusao", "status")
    list_filter = ("status", "rastrear_conclusao", "classe")
    search_fields = ("titulo",)
    autocomplete_fields = ("classe",)
    date_hierarchy = "inicio"


@admin.register(RegraRecorrencia)
class RegraRecorrenciaAdmin(admin.ModelAdmin):
    list_display = ("tipo", "dias", "ignorar_feriados", "data_fim")
    list_filter = ("tipo", "ignorar_feriados")


@admin.register(Ocorrencia)
class OcorrenciaAdmin(admin.ModelAdmin):
    list_display = ("evento", "data", "status_override")
    list_filter = ("status_override",)
    date_hierarchy = "data"
