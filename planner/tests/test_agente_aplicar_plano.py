"""Ferramenta `aplicar_plano` do agente (Fase 1.1, PR B).

Fecha o buraco que o dogfooding de 15/08/2026 achou: `simular_plano` aceitava
`a_partir_de` e não persistia; `replanejar` persistia e não aceitava. Sem uma
ponte, um plano montado com `a_partir_de` — o único jeito de colar estudo na
prova antes do PR A — não tinha como virar calendário.

Quatro invariantes travadas aqui:

1. **Persiste de verdade** e devolve resumo digerido (não a lista crua de
   eventos): o modelo copia, não recalcula.
2. **O `a_partir_de` chega ao banco** — é a razão de a ferramenta existir.
3. **Chamar duas vezes não duplica** — a segunda esbarra em "já promovida".
4. **Nada grava quando o plano é vazio ou a entrada é inválida.**

Ao contrário dos testes de `calcular_plano` (função pura, que recebe `agora`),
esta ferramenta usa `timezone.now()` de verdade quando não recebe `a_partir_de`.
Por isso os prazos aqui são **relativos ao agora**, nunca datas fixas: com data
fixa o teste passa hoje e quebra quando ela ficar no passado.
"""

import json
from datetime import date, timedelta

import pytest
from django.utils import timezone

from planner.models import Classe, Evento, Tarefa
from planner.services import agente

from .factories import ClasseFactory, EventoFactory, TarefaFactory


def _daqui(dias, hora=13):
    """Datetime tz-aware daqui a N dias, às `hora` local."""
    alvo = timezone.localtime(timezone.now()) + timedelta(days=dias)
    return alvo.replace(hour=hora, minute=0, second=0, microsecond=0)


def classe(perfil, nome):
    """Reusa a classe semeada (a unicidade por-dono impede recriar)."""
    return Classe.objects.do_dono(perfil).get(nome=nome)


def _tarefa_estudo(perfil, **kw):
    kw.setdefault("esforco_estimado", 180)
    kw.setdefault("deadline", _daqui(14))
    kw.setdefault("classe", classe(perfil, "Estudar"))
    return TarefaFactory(dono=perfil, **kw)


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Persistência                                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_aplicar_plano_cria_eventos_e_promove_a_tarefa(perfil):
    t = _tarefa_estudo(perfil)
    out = agente._aplicar_plano(perfil, [str(t.id)])

    assert "erro" not in out, out
    assert out["eventos_criados"] > 0
    eventos = Evento.objects.do_dono(perfil).filter(origem_tarefa=t)
    assert eventos.count() == out["eventos_criados"]
    assert (
        sum(int((e.fim - e.inicio).total_seconds() // 60) for e in eventos)
        == t.esforco_estimado
    )
    t.refresh_from_db()
    assert t.status == Tarefa.Status.PROMOVIDA


@pytest.mark.django_db
def test_resumo_e_digerido_por_tarefa(perfil):
    """Contrato de saída: agregado legível, não lista crua de eventos."""
    t = _tarefa_estudo(perfil, titulo="Estudar para a prova")
    out = agente._aplicar_plano(perfil, [str(t.id)])

    assert len(out["aplicado"]) == 1
    resumo = out["aplicado"][0]
    assert resumo["tarefa"] == "Estudar para a prova"
    assert resumo["minutos"] == 180
    assert resumo["sessoes"] == out["eventos_criados"]
    # Datas em ISO local, prontas para o modelo copiar.
    assert date.fromisoformat(resumo["de"]) <= date.fromisoformat(resumo["ate"])
    # Nada de payload cru: sem ids de evento nem campos do model.
    assert set(resumo) == {"tarefa", "sessoes", "minutos", "de", "ate"}


@pytest.mark.django_db
def test_a_partir_de_cola_as_sessoes_no_prazo(perfil):
    """O motivo de a ferramenta existir: o `a_partir_de` chega até o banco."""
    prazo = _daqui(14)
    piso = (prazo - timedelta(days=3)).date()
    t = _tarefa_estudo(perfil, esforco_estimado=120, deadline=prazo)

    out = agente._aplicar_plano(perfil, [str(t.id)], a_partir_de=piso.isoformat())

    assert "erro" not in out, out
    eventos = Evento.objects.do_dono(perfil).filter(origem_tarefa=t)
    assert eventos.exists()
    for ev in eventos:
        assert timezone.localtime(ev.inicio).date() >= piso


@pytest.mark.django_db
def test_a_partir_de_invalido_nao_grava(perfil):
    t = _tarefa_estudo(perfil)
    out = agente._aplicar_plano(perfil, [str(t.id)], a_partir_de="ontem")

    assert out["erro"] == 400
    assert not Evento.objects.do_dono(perfil).filter(origem_tarefa=t).exists()
    t.refresh_from_db()
    assert t.status == Tarefa.Status.INBOX


# --------------------------------------------------------------------------- #
# Guardas                                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_segunda_chamada_nao_duplica(perfil):
    t = _tarefa_estudo(perfil)
    primeira = agente._aplicar_plano(perfil, [str(t.id)])
    criados = Evento.objects.do_dono(perfil).filter(origem_tarefa=t).count()
    assert criados == primeira["eventos_criados"]

    segunda = agente._aplicar_plano(perfil, [str(t.id)])

    assert segunda["erro"] == 422
    assert "já promovida" in _json(segunda)
    assert Evento.objects.do_dono(perfil).filter(origem_tarefa=t).count() == criados


@pytest.mark.django_db
def test_tarefa_inelegivel_devolve_422_sem_gravar(perfil):
    t = TarefaFactory(dono=perfil, deadline=None, esforco_estimado=None)
    out = agente._aplicar_plano(perfil, [str(t.id)])

    assert out["erro"] == 422
    assert not Evento.objects.do_dono(perfil).exists()


@pytest.mark.django_db
def test_tarefa_de_outro_perfil_e_inexistente(perfil, outro_perfil):
    """O escopo entra na busca: a tarefa alheia nem é encontrada."""
    alheia = TarefaFactory(
        dono=outro_perfil,
        classe=ClasseFactory(dono=outro_perfil, nome="Estudo alheio"),
        esforco_estimado=60,
        deadline=_daqui(14),
    )
    out = agente._aplicar_plano(perfil, [str(alheia.id)])

    assert out["erro"] == 422
    assert "inexistente" in _json(out)
    assert not Evento.objects.do_dono(outro_perfil).exists()


@pytest.mark.django_db
def test_plano_sem_espaco_devolve_422_e_nao_grava(perfil):
    """Agenda inteiramente ocupada ⇒ nada é criado, nem parcialmente."""
    prazo = _daqui(5)
    t = _tarefa_estudo(perfil, deadline=prazo)
    EventoFactory(  # cobre toda a janela, inclusive o relaxamento de 24h
        dono=perfil,
        classe=classe(perfil, "Aula"),
        inicio=timezone.now() - timedelta(days=1),
        fim=prazo + timedelta(days=1),
    )
    out = agente._aplicar_plano(perfil, [str(t.id)])

    assert out["erro"] == 422
    assert "nao_alocado" in out["detalhe"]
    assert not Evento.objects.do_dono(perfil).filter(origem_tarefa=t).exists()
    t.refresh_from_db()
    assert t.status == Tarefa.Status.INBOX


# --------------------------------------------------------------------------- #
# Integração com o loop de tool-use                                            #
# --------------------------------------------------------------------------- #
def test_ferramenta_esta_registrada_e_muda_estado():
    ferr = agente.FERRAMENTAS_POR_NOME["aplicar_plano"]
    assert ferr["muda_estado"] is True
    assert ferr["parametros"]["required"] == ["tarefa_ids"]
    assert "a_partir_de" in ferr["parametros"]["properties"]


@pytest.mark.django_db
def test_conversar_aplicando_plano_marca_mudou_estado(monkeypatch, perfil):
    from .test_agente import _instalar_provider, _tc

    t = _tarefa_estudo(perfil)
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="", tool_calls=[_tc("aplicar_plano", tarefa_ids=[str(t.id)])]
            ),
            agente._Turno(texto="Agendei o estudo.", tool_calls=[]),
        ],
    )

    out = agente.conversar(perfil, "aplica o plano", {})

    assert out["mudou_estado"] is True
    assert out["acoes"][0]["ferramenta"] == "aplicar_plano"
    assert out["acoes"][0]["ok"] is True
    assert Evento.objects.do_dono(perfil).filter(origem_tarefa=t).exists()


@pytest.mark.django_db
def test_conversar_com_erro_da_ferramenta_nao_marca_mudou_estado(monkeypatch, perfil):
    from .test_agente import _instalar_provider, _tc

    t = TarefaFactory(dono=perfil, deadline=None, esforco_estimado=None)
    _instalar_provider(
        monkeypatch,
        [
            agente._Turno(
                texto="", tool_calls=[_tc("aplicar_plano", tarefa_ids=[str(t.id)])]
            ),
            agente._Turno(texto="Não deu para agendar.", tool_calls=[]),
        ],
    )

    out = agente.conversar(perfil, "aplica", {})

    assert out["acoes"][0]["ok"] is False
    assert out["mudou_estado"] is False
