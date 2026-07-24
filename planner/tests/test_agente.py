"""Testes do agente conversacional (Marco C4, o cérebro) — provider mockado.

O cérebro (LLM) é substituído por um FakeProvider roteirizado; **as ferramentas
rodam de verdade**, contra o banco de teste.

Até o PR0 da Fase 0B as ferramentas eram HTTP e os testes stubavam `agente._api`
— o que verificava a digestão do payload, mas não que a ferramenta e a API
concordassem. Agora que as ferramentas chamam os services em processo, o stub
sumiu junto: os testes montam dados com as factories e conferem o resultado real.
Contrato deriva do código, não do fake.
"""

import json
from datetime import timedelta

import pytest
from django.core.cache import cache as django_cache
from django.utils import timezone
from rest_framework.test import APIClient

from planner.models import Classe, Evento, Tarefa
from planner.services import agente
from planner.tests.factories import EventoFactory, aware


def classe(nome):
    """As 5 classes padrão já vêm da migration 0002 — criar por factory com um
    desses nomes viola `Classe.nome unique`. Reusa a semeada."""
    return Classe.objects.get(nome=nome)


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "agente-tests",
        }
    }
    django_cache.clear()
    yield
    django_cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def eager():
    from config.celery import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    app.conf.task_store_eager_result = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_store_eager_result = False


class FakeProvider:
    """Cérebro roteirizado: devolve `_Turno`s na ordem dada e registra os
    resultados de ferramenta recebidos."""

    def __init__(self, turnos):
        self._turnos = list(turnos)
        self.resultados = []

    def gerar(self):
        return self._turnos.pop(0)

    def responder_ferramentas(self, resultados):
        self.resultados.append(resultados)
        return self._turnos.pop(0)


def _instalar_provider(monkeypatch, turnos):
    prov = FakeProvider(turnos)
    monkeypatch.setattr(agente, "_criar_provider", lambda hist, msg: prov)
    return prov


def _tc(nome, **args):
    return agente._ToolCall(id="1", nome=nome, args=args)


# --------------------------------------------------------------------------- #
# Loop de tool-use                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_conversar_executa_ferramenta_e_responde(monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("listar_classes")]),
            agente._Turno(texto="Você tem a classe Prova.", tool_calls=[]),
        ],
    )

    out = agente.conversar("quais classes?", {"hoje": "2026-07-04"})

    assert out["resposta"] == "Você tem a classe Prova."
    assert out["ia_indisponivel"] is False
    assert [a["ferramenta"] for a in out["acoes"]] == ["listar_classes"]
    assert out["acoes"][0]["ok"] is True
    # listar_classes é leitura → não recarrega o calendário.
    assert out["mudou_estado"] is False


@pytest.mark.django_db
def test_conversar_criar_tarefa_cria_de_verdade_e_marca_mudou_estado(monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="",
                tool_calls=[
                    _tc("criar_tarefa", titulo="Física 2", deadline="2026-07-10")
                ],
            ),
            agente._Turno(texto="Criei a tarefa.", tool_calls=[]),
        ],
    )

    out = agente.conversar("adiciona Física 2", {"hoje": "2026-07-04"})

    assert out["mudou_estado"] is True
    assert out["acoes"][0]["muda_estado"] is True
    # O ganho de chamar o service em processo: dá para afirmar que a escrita
    # aconteceu, não só que a ferramenta devolveu um dict bonito.
    assert Tarefa.objects.filter(titulo="Física 2").exists()


@pytest.mark.django_db
def test_conversar_replanejar_so_muda_estado_ao_aplicar(monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="", tool_calls=[_tc("replanejar", dias_bloqueados=["2026-07-05"])]
            ),
            agente._Turno(texto="Simulei.", tool_calls=[]),
        ],
    )
    simulou = agente.conversar("como fica sem sábado?", {})
    assert simulou["mudou_estado"] is False  # aplicar ausente → só simulação

    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="",
                tool_calls=[
                    _tc("replanejar", dias_bloqueados=["2026-07-05"], aplicar=True)
                ],
            ),
            agente._Turno(texto="Livrei seu sábado.", tool_calls=[]),
        ],
    )
    aplicou = agente.conversar("livra meu sábado", {})
    assert aplicou["mudou_estado"] is True


@pytest.mark.django_db
def test_conversar_ferramenta_desconhecida_nao_estoura(monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("inexistente")]),
            agente._Turno(texto="Não consegui.", tool_calls=[]),
        ],
    )
    out = agente.conversar("faz algo", {})
    assert out["resposta"] == "Não consegui."
    assert out["acoes"] == []  # nome desconhecido não vira ação registrada


@pytest.mark.django_db
def test_conversar_erro_de_ferramenta_marca_ok_false(monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="",
                tool_calls=[_tc("criar_tarefa", titulo="X", classe_id="nao-existe")],
            ),
            agente._Turno(texto="Faltou a classe.", tool_calls=[]),
        ],
    )
    out = agente.conversar("cria X", {})
    assert out["acoes"][0]["ok"] is False
    assert out["mudou_estado"] is False  # erro não recarrega o calendário
    assert not Tarefa.objects.exists()  # nada foi gravado


# --------------------------------------------------------------------------- #
# Ferramentas, isoladas                                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_criar_tarefa_com_classe_invalida_devolve_erro_acionavel():
    """E2E com o 7B: o modelo chuta classe_id e desiste do erro cru. O erro
    precisa voltar com as classes reais + dica para o modelo se corrigir."""
    estudar = classe("Estudar")

    out = agente._criar_tarefa("X", classe_id="1")

    assert out["erro"] == 400
    assert {"id": str(estudar.id), "nome": "Estudar"} in out["classes_disponiveis"]
    assert "criar_tarefa" in out["dica"]
    assert not Tarefa.objects.exists()


@pytest.mark.django_db
def test_criar_tarefa_erro_sem_classe_id_nao_devolve_classes():
    """Erro que não é de classe (ex.: deadline inválida) passa reto, sem o
    payload extra de classes."""
    out = agente._criar_tarefa("X", deadline="ontem")
    assert out["erro"] == 400
    assert "classes_disponiveis" not in out
    assert "deadline" in out["detalhe"]


@pytest.mark.django_db
def test_criar_tarefa_normaliza_deadline_utc_e_naive():
    """ "17h" dito pelo usuário é hora LOCAL; o 7B escreve 17:00Z (=14h local).
    Naive e UTC-zero viram hora de parede local; offset real é respeitado."""
    esperado = "2026-07-08T17:00:00-03:00"

    for entrada in (
        "2026-07-08T17:00:00Z",
        "2026-07-08T17:00",
        "2026-07-08T17:00:00-03:00",
    ):
        out = agente._criar_tarefa("X", deadline=entrada)
        assert "erro" not in out, out
        assert out["deadline"] == esperado, entrada
        # O banco guarda em UTC; o que importa é o instante, não a grafia.
        gravado = Tarefa.objects.get(pk=out["id"]).deadline
        assert timezone.localtime(gravado).isoformat() == esperado


@pytest.mark.django_db
def test_listar_classes_ordenado_e_com_id_string():
    saida = agente._listar_classes()
    # As 5 padrão da migration 0002, em ordem alfabética.
    assert [c["nome"] for c in saida] == sorted(c["nome"] for c in saida)
    assert {"Aula", "Estudar", "Prova"} <= {c["nome"] for c in saida}
    # id como str: vai direto para o JSON da ferramenta, sem UUID cru.
    assert all(isinstance(c["id"], str) for c in saida)


@pytest.mark.django_db
def test_consultar_agenda_digere_por_dia_em_horario_local():
    """A API fala UTC e o payload cru fazia o 7B alucinar o resumo. A
    ferramenta entrega dias prontos, horário local hh:mm — o modelo copia."""
    basicas = classe("Tarefas básicas")
    aula = classe("Aula")
    # 19:00 local = 22:00Z
    EventoFactory(
        titulo="Academia",
        classe=basicas,
        inicio=aware(2026, 7, 6, 19),
        fim=aware(2026, 7, 6, 20, 30),
        descricao="ruído que não deve vazar",
    )
    EventoFactory(
        titulo="Cálculo II",
        classe=aula,
        inicio=aware(2026, 7, 6, 8),
        fim=aware(2026, 7, 6, 10),
    )
    EventoFactory(
        titulo="Inglês",
        classe=aula,
        inicio=aware(2026, 7, 11, 9),
        fim=aware(2026, 7, 11, 11),
    )

    out = agente._consultar_agenda(
        "2026-07-06T00:00:00-03:00", "2026-07-12T00:00:00-03:00"
    )

    assert [d["data"] for d in out] == ["2026-07-06", "2026-07-11"]
    assert out[0]["dia_da_semana"] == "segunda-feira"
    seg = out[0]["eventos"]
    assert [e["titulo"] for e in seg] == ["Cálculo II", "Academia"]  # ordenado
    assert seg[1]["inicio"] == "19:00" and seg[1]["fim"] == "20:30"  # local
    assert "descricao" not in seg[1]  # ruído do payload não vaza


@pytest.mark.django_db
def test_consultar_agenda_normaliza_janela_naive_e_data_pura():
    """O 7B manda '2026-07-06' ou datetime sem offset; a janela exige tz-aware.
    A ferramenta normaliza (data pura como fim vira 23:59) — era 400 no E2E."""
    EventoFactory(
        titulo="Só neste dia",
        inicio=aware(2026, 7, 6, 8),
        fim=aware(2026, 7, 6, 10),
    )

    # Data pura nas duas pontas: o fim vira 23:59 e o evento do dia entra.
    out = agente._consultar_agenda("2026-07-06", "2026-07-06")
    assert [d["data"] for d in out] == ["2026-07-06"]

    # Naive vira aware; offset explícito é preservado.
    out = agente._consultar_agenda("2026-07-06T07:00", "2026-07-06T12:00:00-03:00")
    assert [e["titulo"] for e in out[0]["eventos"]] == ["Só neste dia"]

    # Não-ISO vira erro acionável, em vez de 400 vindo da API.
    erro = agente._consultar_agenda("semana que vem", "???")
    assert erro["erro"] == 400


@pytest.mark.django_db
def test_consultar_agenda_recusa_janela_maior_que_o_teto():
    erro = agente._consultar_agenda("2026-01-01", "2026-12-31")
    assert erro["erro"] == 400
    assert "92 dias" in erro["detalhe"]


@pytest.mark.django_db
def test_listar_pendentes_digere():
    estudar = classe("Estudar")
    agora = timezone.now()
    EventoFactory(
        titulo="Revisar Cálculo",
        classe=estudar,
        inicio=agora - timedelta(hours=3),
        fim=agora - timedelta(hours=2),
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )

    out = agente._listar_pendentes()

    assert len(out) == 1
    assert out[0]["titulo"] == "Revisar Cálculo"
    assert out[0]["classe"] == "Estudar"
    assert out[0]["venceu_em"] == timezone.localtime(
        agora - timedelta(hours=2)
    ).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# Grounding dos FATOS                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_conversar_injeta_classes_nos_fatos(monkeypatch):
    """Grounding: as classes reais entram nos FATOS do pedido — o 7B copia o
    id em vez de precisar do salto listar_classes → criar_tarefa (que ele
    não faz: chuta ids, vimos no E2E)."""
    estudar = classe("Estudar")
    pedidos = []

    def fake_criar_provider(historico, pedido):
        pedidos.append(pedido)
        return FakeProvider([agente._Turno(texto="ok", tool_calls=[])])

    monkeypatch.setattr(agente, "_criar_provider", fake_criar_provider)
    agente.conversar("oi", {"hoje": "2026-07-04"})
    assert str(estudar.id) in pedidos[0]  # classes viraram FATOS
    assert "hoje" in pedidos[0]  # contexto original preservado


@pytest.mark.django_db
def test_conversar_injeta_datas_nos_fatos(monkeypatch):
    """Data é aritmética: o dicionário `datas` usa as palavras do usuário como
    chave ("próxima segunda-feira") — a resolução vira busca literal (no E2E o
    7B apontava uma sexta para "segunda que vem")."""
    pedidos = []

    def fake_criar_provider(historico, pedido):
        pedidos.append(pedido)
        return FakeProvider([agente._Turno(texto="ok", tool_calls=[])])

    monkeypatch.setattr(agente, "_criar_provider", fake_criar_provider)
    agente.conversar("o que tenho sexta?", {})
    fatos = json.loads(pedidos[0].split("\n\nPedido")[0].split(":\n", 1)[1])

    hoje = timezone.localdate()
    assert fatos["datas"]["hoje"].startswith(hoje.isoformat())
    assert fatos["datas"]["amanhã"] == (hoje + timedelta(days=1)).isoformat()
    # As 7 chaves "próxima <dia>" cobrem uma volta completa da semana.
    proximas = [k for k in fatos["datas"] if k.startswith("próxima ")]
    assert len(proximas) == 7
    prox_seg = next(
        d for i in range(1, 8) if (d := hoje + timedelta(days=i)).weekday() == 0
    )
    assert fatos["datas"]["próxima segunda-feira"] == prox_seg.isoformat()


def test_conversar_desligado_levanta(settings):
    settings.AGENTE_ENABLED = False
    with pytest.raises(agente.AgenteIndisponivel):
        agente.conversar("oi", {})


# --------------------------------------------------------------------------- #
# Endpoint 202 → polling → pronto                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_endpoint_chat_fluxo_completo(api, eager, monkeypatch):
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(texto="", tool_calls=[_tc("listar_classes")]),
            agente._Turno(texto="Tem Prova.", tool_calls=[]),
        ],
    )

    r = api.post(
        "/api/v1/planejamento/agente/chat",
        {
            "conversa_id": "conv-1",
            "mensagem": "quais classes?",
            "contexto": {"hoje": "2026-07-04"},
        },
        format="json",
    )
    assert r.status_code == 202
    job_id = r.data["job_id"]

    s = api.get(f"/api/v1/planejamento/agente/chat/{job_id}")
    assert s.status_code == 200
    assert s.data["status"] == "pronto"
    assert s.data["resultado"]["resposta"] == "Tem Prova."


@pytest.mark.django_db
def test_endpoint_degrada_sem_cerebro(api, eager, monkeypatch):
    def _cai(hist, msg):
        raise agente.AgenteIndisponivel("provider fora")

    monkeypatch.setattr(agente, "_criar_provider", _cai)

    r = api.post(
        "/api/v1/planejamento/agente/chat",
        {"conversa_id": "conv-2", "mensagem": "oi"},
        format="json",
    )
    assert r.status_code == 202
    s = api.get(f"/api/v1/planejamento/agente/chat/{r.data['job_id']}")
    assert s.data["status"] == "pronto"
    assert s.data["resultado"]["ia_indisponivel"] is True


@pytest.mark.django_db
def test_endpoint_memoria_da_conversa_reenvia_historico(api, eager, monkeypatch):
    capturado = {}

    def _fake_criar(historico, mensagem):
        capturado["historico"] = list(historico)
        return FakeProvider([agente._Turno(texto="ok", tool_calls=[])])

    monkeypatch.setattr(agente, "_criar_provider", _fake_criar)

    corpo = {"conversa_id": "conv-3", "mensagem": "primeira"}
    api.post("/api/v1/planejamento/agente/chat", corpo, format="json")
    assert capturado["historico"] == []  # 1º turno: sem memória

    api.post(
        "/api/v1/planejamento/agente/chat",
        {"conversa_id": "conv-3", "mensagem": "segunda"},
        format="json",
    )
    # 2º turno: o texto do 1º turno (user+assistant) foi reenviado.
    assert {"role": "user", "content": "primeira"} in capturado["historico"]
    assert {"role": "assistant", "content": "ok"} in capturado["historico"]
