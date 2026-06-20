"""Testes de contrato da API (Handoff §14, exceto escopo por dono).

Projeto local/single-user: API aberta, sem auth — não há testes de isolamento
por dono.
"""

import pytest
from rest_framework.test import APIClient

from planner.models import Evento, Tarefa

from .factories import ClasseFactory, EventoFactory, TarefaFactory, aware

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def test_delete_classe_em_uso_retorna_409(api):
    classe = ClasseFactory()
    EventoFactory(classe=classe)
    resp = api.delete(f"/api/v1/classes/{classe.id}/")
    assert resp.status_code == 409


def test_delete_classe_sem_uso_retorna_204(api):
    classe = ClasseFactory()
    resp = api.delete(f"/api/v1/classes/{classe.id}/")
    assert resp.status_code == 204


def test_evento_fim_antes_de_inicio_retorna_400(api):
    classe = ClasseFactory()
    resp = api.post(
        "/api/v1/eventos/",
        {
            "titulo": "Invalido",
            "inicio": "2026-06-01T10:00:00-03:00",
            "fim": "2026-06-01T09:00:00-03:00",
            "classe_id": str(classe.id),
        },
        format="json",
    )
    assert resp.status_code == 400
    assert "fim" in resp.json()


def test_lista_eventos_sem_janela_retorna_400(api):
    resp = api.get("/api/v1/eventos/")
    assert resp.status_code == 400


def test_lista_eventos_janela_maior_que_92_dias_retorna_400(api):
    resp = api.get(
        "/api/v1/eventos/"
        "?inicio=2026-01-01T00:00:00-03:00&fim=2026-06-01T00:00:00-03:00"
    )
    assert resp.status_code == 400


def test_lista_eventos_data_naive_retorna_400(api):
    resp = api.get(
        "/api/v1/eventos/?inicio=2026-06-01T00:00:00&fim=2026-06-10T00:00:00-03:00"
    )
    assert resp.status_code == 400


def test_promover_herda_classe_e_rastreamento(api):
    classe = ClasseFactory(rastreia_conclusao=True)
    tarefa = TarefaFactory(classe=classe, esforco_estimado=90)
    resp = api.post(
        f"/api/v1/tarefas/{tarefa.id}/promover/",
        {"inicio": "2026-06-25T13:00:00-03:00"},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["classe"]["id"] == str(classe.id)
    assert body["rastrear_conclusao"] is True
    assert body["status"] == Evento.Status.AGENDADO
    assert body["origem_tarefa"] == str(tarefa.id)
    assert body["fim"].startswith("2026-06-25T14:30")  # inicio + 90 min

    tarefa.refresh_from_db()
    assert tarefa.status == Tarefa.Status.PROMOVIDA


def test_promover_sem_fim_nem_esforco_usa_uma_hora(api):
    classe = ClasseFactory()
    tarefa = TarefaFactory(classe=classe, esforco_estimado=None)
    resp = api.post(
        f"/api/v1/tarefas/{tarefa.id}/promover/",
        {"inicio": "2026-06-25T13:00:00-03:00"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["fim"].startswith("2026-06-25T14:00")


def test_pendentes_lista_vencidos_rastreaveis(api):
    classe = ClasseFactory(rastreia_conclusao=True)
    EventoFactory(
        classe=classe,
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        inicio=aware(2020, 1, 1, 8),
        fim=aware(2020, 1, 1, 10),
    )
    resp = api.get("/api/v1/pendentes")
    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 1
    assert corpo[0]["status_efetivo"] == "PENDENTE"


def test_cor_invalida_retorna_400(api):
    resp = api.post(
        "/api/v1/classes/",
        {"nome": "Cor ruim", "cor": "#GGGGGG"},
        format="json",
    )
    assert resp.status_code == 400
    assert "cor" in resp.json()
