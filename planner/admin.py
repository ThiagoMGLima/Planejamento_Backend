"""Registro no Django admin — apoio a debug/seed (Handoff §13, Marco 1).

**Escopo no admin (decisão Q6 do plano da Fase 0B/PR1):** o painel enxerga
todos os perfis (`sem_escopo()`), mas cada listagem traz `dono` como coluna e
como filtro — na prática se trabalha num perfil por vez, escolhendo-o na barra
lateral. É o modo de trabalho pedido sem inventar no admin uma noção de "perfil
atual" que o Django não tem (seleção em sessão, seletor no topo, cada
ModelAdmin escopado nela).

Investigar o problema de um testador vira trocar o filtro, não trocar de conta.
O isolamento que importa é o da API — este painel só o operador acessa.
"""

from django.contrib import admin

from .models import (
    Classe,
    Evento,
    FeriadoLocal,
    Ocorrencia,
    Perfil,
    RegraRecorrencia,
    Tarefa,
)


class EscopoGlobalAdmin(admin.ModelAdmin):
    """Base dos models por-dono: o manager recusa consulta sem escopo, e é aqui
    que a varredura global é declarada — explícita e num lugar só."""

    def get_queryset(self, request):
        return self.model.objects.sem_escopo()


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ("email", "nome", "plano", "trial_ate", "supabase_id", "criado_em")
    list_filter = ("plano",)
    search_fields = ("email", "nome")


@admin.register(Classe)
class ClasseAdmin(EscopoGlobalAdmin):
    list_display = ("nome", "dono", "cor", "rastreia_conclusao", "criado_em")
    list_filter = ("dono",)
    search_fields = ("nome",)


@admin.register(Tarefa)
class TarefaAdmin(EscopoGlobalAdmin):
    list_display = ("titulo", "dono", "classe", "status", "deadline", "criado_em")
    list_filter = ("dono", "status", "classe")
    search_fields = ("titulo",)
    autocomplete_fields = ("classe",)


@admin.register(Evento)
class EventoAdmin(EscopoGlobalAdmin):
    list_display = (
        "titulo",
        "dono",
        "classe",
        "inicio",
        "fim",
        "rastrear_conclusao",
        "status",
    )
    list_filter = ("dono", "status", "rastrear_conclusao", "classe")
    search_fields = ("titulo",)
    autocomplete_fields = ("classe",)
    date_hierarchy = "inicio"


@admin.register(RegraRecorrencia)
class RegraRecorrenciaAdmin(EscopoGlobalAdmin):
    list_display = ("tipo", "dono", "dias", "ignorar_feriados", "data_fim")
    list_filter = ("dono", "tipo", "ignorar_feriados")


@admin.register(Ocorrencia)
class OcorrenciaAdmin(admin.ModelAdmin):
    # Sem `dono` próprio: herda pelo evento (CASCADE, não-nulo).
    list_display = ("evento", "data", "status_override")
    list_filter = ("status_override", "evento__dono")
    date_hierarchy = "data"


@admin.register(FeriadoLocal)
class FeriadoLocalAdmin(EscopoGlobalAdmin):
    list_display = ("nome", "dono", "dia", "mes", "ano")
    list_filter = ("dono", "mes")
    search_fields = ("nome",)
