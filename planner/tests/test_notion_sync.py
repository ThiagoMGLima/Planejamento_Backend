"""Testes da sincronização Notion → Inbox (HTTP do Notion sempre mockado).

Cobre: extração/criação de Tarefa, mapeamento de classe, datas (pura vs ISO),
idempotência por `origem_externa_id`, coleta de erros por página, os estados
desligado/indisponível e o endpoint POST /notion/sync.
"""

from unittest import mock

import pytest
import requests
from rest_framework.test import APIClient

from planner.models import Classe, Tarefa
from planner.services import notion_sync as N


@pytest.fixture(autouse=True)
def _notion_config(settings):
    settings.NOTION_TOKEN = "secret"
    settings.NOTION_DATABASE_ID = "db-123"
    settings.NOTION_DEADLINE_HORA_PADRAO = "23:59"


@pytest.fixture
def classe_trabalho(db):
    return Classe.objects.get_or_create(nome="Trabalho", defaults={"cor": "#abcdef"})[0]


def _page(
    pid,
    titulo="Trabalho de Cálculo",
    *,
    prazo="2026-06-30",
    esforco=240,
    classe="Trabalho",
):
    props = {
        N.PROP_TITULO: {"title": [{"plain_text": titulo}] if titulo else []},
        N.PROP_PRAZO: {"date": {"start": prazo} if prazo else None},
        N.PROP_ESFORCO: {"number": esforco},
        N.PROP_CLASSE: {"select": {"name": classe} if classe else None},
        N.PROP_STATUS: {"select": {"name": "Nova"}},
    }
    return {"id": pid, "properties": props}


def _fake_requests(paginas):
    """MagicMock no lugar de `requests`: query devolve `paginas`, patch dá 200."""
    fake = mock.MagicMock()
    fake.RequestException = requests.RequestException
    query_resp = mock.Mock(status_code=200)
    query_resp.json.return_value = {"results": paginas, "has_more": False}
    fake.post.return_value = query_resp
    fake.patch.return_value = mock.Mock(status_code=200)
    return fake


# --------------------------------------------------------------------------- #
# sincronizar — caminho feliz                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_importa_cria_tarefa_e_marca_importada(classe_trabalho):
    fake = _fake_requests([_page("p1")])
    with mock.patch.object(N, "requests", fake):
        resumo = N.sincronizar()

    assert resumo == {"importadas": 1, "ignoradas": 0, "erros": []}
    t = Tarefa.objects.get(origem_externa_id="p1")
    assert t.titulo == "Trabalho de Cálculo"
    assert t.esforco_estimado == 240
    assert t.classe_id == classe_trabalho.id
    assert t.status == Tarefa.Status.INBOX
    # marcou a página como Importada (1 PATCH)
    assert fake.patch.call_count == 1


@pytest.mark.django_db
def test_data_pura_usa_hora_padrao(classe_trabalho):
    from django.utils import timezone

    fake = _fake_requests([_page("p1", prazo="2026-06-30")])
    with mock.patch.object(N, "requests", fake):
        N.sincronizar()
    dl = timezone.localtime(Tarefa.objects.get(origem_externa_id="p1").deadline)
    assert (dl.hour, dl.minute) == (23, 59)
    assert dl.date().isoformat() == "2026-06-30"


@pytest.mark.django_db
def test_data_com_hora_iso_e_preservada(classe_trabalho):
    from django.utils import timezone

    fake = _fake_requests([_page("p1", prazo="2026-06-30T18:00:00-03:00")])
    with mock.patch.object(N, "requests", fake):
        N.sincronizar()
    dl = timezone.localtime(Tarefa.objects.get(origem_externa_id="p1").deadline)
    assert dl.hour == 18


@pytest.mark.django_db
def test_classe_desconhecida_fica_nula(classe_trabalho):
    fake = _fake_requests([_page("p1", classe="Inexistente")])
    with mock.patch.object(N, "requests", fake):
        N.sincronizar()
    assert Tarefa.objects.get(origem_externa_id="p1").classe_id is None


# --------------------------------------------------------------------------- #
# idempotência e erros                                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_idempotente_nao_duplica(classe_trabalho):
    fake = _fake_requests([_page("p1")])
    with mock.patch.object(N, "requests", fake):
        N.sincronizar()
        resumo2 = N.sincronizar()  # mesma página de novo
    assert Tarefa.objects.filter(origem_externa_id="p1").count() == 1
    assert resumo2 == {"importadas": 0, "ignoradas": 1, "erros": []}


@pytest.mark.django_db
def test_titulo_vazio_vira_erro_sem_criar(classe_trabalho):
    fake = _fake_requests([_page("p1", titulo="")])
    with mock.patch.object(N, "requests", fake):
        resumo = N.sincronizar()
    assert resumo["importadas"] == 0
    assert resumo["erros"] == [{"page_id": "p1", "motivo": "título vazio"}]
    assert not Tarefa.objects.filter(origem_externa_id="p1").exists()


# --------------------------------------------------------------------------- #
# estados de borda                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_desligado_sem_token(settings):
    settings.NOTION_TOKEN = ""
    with pytest.raises(N.NotionDesligado):
        N.sincronizar()


@pytest.mark.django_db
def test_query_nao_200_vira_indisponivel(classe_trabalho):
    fake = _fake_requests([])
    fake.post.return_value = mock.Mock(status_code=401, text="unauthorized")
    with mock.patch.object(N, "requests", fake):
        with pytest.raises(N.NotionIndisponivel):
            N.sincronizar()


# --------------------------------------------------------------------------- #
# endpoint POST /notion/sync                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_endpoint_resumo_200():
    api = APIClient()
    with mock.patch.object(
        N, "sincronizar", return_value={"importadas": 2, "ignoradas": 1, "erros": []}
    ):
        resp = api.post("/api/v1/notion/sync")
    assert resp.status_code == 200
    assert resp.data["importadas"] == 2


@pytest.mark.django_db
def test_endpoint_desligado_400():
    api = APIClient()
    with mock.patch.object(N, "sincronizar", side_effect=N.NotionDesligado("x")):
        resp = api.post("/api/v1/notion/sync")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_endpoint_indisponivel_503():
    api = APIClient()
    with mock.patch.object(N, "sincronizar", side_effect=N.NotionIndisponivel("x")):
        resp = api.post("/api/v1/notion/sync")
    assert resp.status_code == 503
