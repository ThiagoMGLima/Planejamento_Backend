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


def test_promover_acompanha_conclusao_por_default(api):
    # Default do produto: todo evento acompanha conclusão, mesmo quando a classe
    # de origem não rastreia (rastreia_conclusao=False).
    classe = ClasseFactory(rastreia_conclusao=False)
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


def test_planejar_cria_uma_sessao_por_evento(api):
    classe = ClasseFactory(rastreia_conclusao=False)
    tarefa = TarefaFactory(classe=classe, esforco_estimado=360)  # 6h de produção
    resp = api.post(
        f"/api/v1/tarefas/{tarefa.id}/planejar/",
        {
            "sessoes": [
                {
                    "inicio": "2026-06-24T19:00:00-03:00",
                    "fim": "2026-06-24T21:00:00-03:00",
                },
                {
                    "inicio": "2026-06-26T19:00:00-03:00",
                    "fim": "2026-06-26T21:00:00-03:00",
                },
                {
                    "inicio": "2026-06-28T19:00:00-03:00",
                    "fim": "2026-06-28T21:00:00-03:00",
                },
            ]
        },
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body) == 3
    # Todo evento-sessão acompanha conclusão e aponta para a tarefa de origem.
    assert all(ev["rastrear_conclusao"] is True for ev in body)
    assert all(ev["origem_tarefa"] == str(tarefa.id) for ev in body)
    assert all(ev["classe"]["id"] == str(classe.id) for ev in body)

    tarefa.refresh_from_db()
    assert tarefa.status == Tarefa.Status.PROMOVIDA
    assert tarefa.eventos.count() == 3


def test_planejar_sem_sessoes_retorna_400(api):
    tarefa = TarefaFactory(classe=ClasseFactory())
    resp = api.post(
        f"/api/v1/tarefas/{tarefa.id}/planejar/",
        {"sessoes": []},
        format="json",
    )
    assert resp.status_code == 400


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


# --- Planejamento multitarefa (calcular / aplicar) --------------------------- #

# Segunda-feira fixa para tornar o cálculo determinístico (independe do relógio).
A_PARTIR_DE = "2026-06-01T08:00:00-03:00"


def test_calcular_retorna_plano_e_preferencias_usadas(api):
    classe = ClasseFactory()
    tarefa = TarefaFactory(
        classe=classe, esforco_estimado=240, deadline=aware(2026, 6, 5, 18)
    )
    resp = api.post(
        "/api/v1/planejamento/calcular",
        {"tarefa_ids": [str(tarefa.id)], "a_partir_de": A_PARTIR_DE},
        format="json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sum(s["dur_min"] for s in body["sessoes"]) == 240
    assert all(s["tarefa_id"] == str(tarefa.id) for s in body["sessoes"])
    assert all(s["classe_id"] == str(classe.id) for s in body["sessoes"])
    assert body["nao_alocado"] == []
    assert body["preferencias_usadas"]["janela_inicio"] == "08:00"


def test_calcular_tarefa_sem_deadline_retorna_422(api):
    valida = TarefaFactory(
        classe=ClasseFactory(), esforco_estimado=120, deadline=aware(2026, 6, 5, 18)
    )
    invalida = TarefaFactory(
        classe=ClasseFactory(), esforco_estimado=60
    )  # sem deadline
    resp = api.post(
        "/api/v1/planejamento/calcular",
        {"tarefa_ids": [str(valida.id), str(invalida.id)]},
        format="json",
    )
    assert resp.status_code == 422
    invalidas = resp.json()["tarefas_invalidas"]
    assert len(invalidas) == 1
    assert invalidas[0]["tarefa_id"] == str(invalida.id)
    assert "deadline" in invalidas[0]["motivo"]


def test_calcular_sem_tarefa_ids_retorna_400(api):
    resp = api.post("/api/v1/planejamento/calcular", {"tarefa_ids": []}, format="json")
    assert resp.status_code == 400


def test_calcular_tarefa_inexistente_retorna_422(api):
    import uuid

    resp = api.post(
        "/api/v1/planejamento/calcular",
        {"tarefa_ids": [str(uuid.uuid4())]},
        format="json",
    )
    assert resp.status_code == 422
    assert resp.json()["tarefas_invalidas"][0]["motivo"] == "tarefa inexistente"


def test_aplicar_cria_eventos_de_varias_tarefas_e_promove(api):
    classe = ClasseFactory()
    t1 = TarefaFactory(classe=classe, esforco_estimado=120)
    t2 = TarefaFactory(classe=classe, esforco_estimado=90)
    resp = api.post(
        "/api/v1/planejamento/aplicar",
        {
            "sessoes": [
                {
                    "tarefa_id": str(t1.id),
                    "inicio": "2026-06-22T19:00:00-03:00",
                    "fim": "2026-06-22T21:00:00-03:00",
                },
                {
                    "tarefa_id": str(t2.id),
                    "inicio": "2026-06-23T19:00:00-03:00",
                    "fim": "2026-06-23T20:30:00-03:00",
                },
            ]
        },
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body) == 2
    assert all(ev["rastrear_conclusao"] is True for ev in body)
    assert {ev["origem_tarefa"] for ev in body} == {str(t1.id), str(t2.id)}

    t1.refresh_from_db()
    t2.refresh_from_db()
    assert t1.status == Tarefa.Status.PROMOVIDA
    assert t2.status == Tarefa.Status.PROMOVIDA


def test_aplicar_tarefa_sem_classe_retorna_400(api):
    tarefa = TarefaFactory(classe=None, esforco_estimado=60)
    resp = api.post(
        "/api/v1/planejamento/aplicar",
        {
            "sessoes": [
                {
                    "tarefa_id": str(tarefa.id),
                    "inicio": "2026-06-22T19:00:00-03:00",
                    "fim": "2026-06-22T20:00:00-03:00",
                }
            ]
        },
        format="json",
    )
    assert resp.status_code == 400
    assert "classe_id" in resp.json()


def test_aplicar_fim_antes_de_inicio_retorna_400(api):
    tarefa = TarefaFactory(classe=ClasseFactory(), esforco_estimado=60)
    resp = api.post(
        "/api/v1/planejamento/aplicar",
        {
            "sessoes": [
                {
                    "tarefa_id": str(tarefa.id),
                    "inicio": "2026-06-22T21:00:00-03:00",
                    "fim": "2026-06-22T19:00:00-03:00",
                }
            ]
        },
        format="json",
    )
    assert resp.status_code == 400
