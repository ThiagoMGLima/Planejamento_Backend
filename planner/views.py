"""Views da API. No Marco 1, apenas o health check (Handoff §13).

ViewSets de classes/tarefas/eventos entram no Marco 2.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """GET /api/v1/health → 200. Usado pelo healthcheck do Docker."""
    return Response({"status": "ok"})
