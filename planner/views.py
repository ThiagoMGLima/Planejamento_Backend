"""Views da API (Handoff §8).

Marco 2: CRUD de Classe, Tarefa e Evento + ação `promover`. A janela de eventos
com expansão de ocorrências, `concluir`/`remarcar`, pendentes e feriados entram
no Marco 3.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import ProtectedError
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .filters import TarefaFilter
from .models import Classe, Evento, Tarefa
from .serializers import (
    ClasseSerializer,
    EventoSerializer,
    PromoverSerializer,
    TarefaSerializer,
)


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """GET /api/v1/health → 200. Usado pelo healthcheck do Docker."""
    return Response({"status": "ok"})


class ClasseViewSet(viewsets.ModelViewSet):
    queryset = Classe.objects.all()
    serializer_class = ClasseSerializer

    def destroy(self, request, *args, **kwargs):
        # DELETE bloqueado (409) se houver eventos na classe (on_delete=PROTECT).
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "detail": "Classe em uso por um ou mais eventos; "
                    "reatribua-os antes de apagar."
                },
                status=http_status.HTTP_409_CONFLICT,
            )


class TarefaViewSet(viewsets.ModelViewSet):
    queryset = Tarefa.objects.select_related("classe").all()
    serializer_class = TarefaSerializer
    filterset_class = TarefaFilter

    @action(detail=True, methods=["post"])
    def promover(self, request, pk=None):
        """Arrasto Inbox → calendário (Handoff §8.2).

        Cria um Evento herdando classe e rastrear_conclusao, liga origem_tarefa
        e marca a Tarefa como PROMOVIDA. Sem `fim`, usa esforco_estimado ou 1h.
        """
        tarefa = self.get_object()
        entrada = PromoverSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        classe = dados.get("classe") or tarefa.classe
        if classe is None:
            return Response(
                {"classe_id": ["Tarefa sem classe; informe classe_id."]},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        inicio = dados["inicio"]
        fim = dados.get("fim")
        if fim is None:
            if tarefa.esforco_estimado:
                fim = inicio + timedelta(minutes=tarefa.esforco_estimado)
            else:
                fim = inicio + timedelta(hours=1)

        with transaction.atomic():
            evento = Evento.objects.create(
                titulo=tarefa.titulo,
                descricao=tarefa.descricao,
                inicio=inicio,
                fim=fim,
                classe=classe,
                rastrear_conclusao=classe.rastreia_conclusao,
                status=(
                    Evento.Status.AGENDADO if classe.rastreia_conclusao else None
                ),
                origem_tarefa=tarefa,
            )
            tarefa.status = Tarefa.Status.PROMOVIDA
            tarefa.save(update_fields=["status", "atualizado_em"])

        return Response(
            EventoSerializer(evento).data, status=http_status.HTTP_201_CREATED
        )


class EventoViewSet(viewsets.ModelViewSet):
    queryset = Evento.objects.select_related(
        "classe", "regra_recorrencia", "origem_tarefa"
    ).all()
    serializer_class = EventoSerializer
