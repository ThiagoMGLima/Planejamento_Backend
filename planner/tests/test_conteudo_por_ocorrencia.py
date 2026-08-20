"""Conteúdo por ocorrência (Fase 1.2, PR A).

A aula é um bloco fixo recorrente; o que varia por semana é o CONTEÚDO daquela
data — inclusive "hoje tem prova". Antes disto, `Ocorrencia` só sabia sobrescrever
horário e status, então lançar um planejamento de ensino degenerava em um evento
avulso por semana e a série recorrente sumia.

O que estes testes fixam:

1. **Resolução na leitura, num lugar só.** `montar_ocorrencia` decide o título, a
   descrição e a classe efetivos daquela data; view e agente leem o resultado. Se
   a resolução vazar para o consumidor, dois consumidores discordam.
2. **Override não contamina a série.** É a razão de o conteúdo morar na
   `Ocorrencia` e não no `Evento`.
3. **Vazio herda.** `blank=True` sem nulo: string vazia é "não disse nada", e não
   "apague o título da série".
4. **Concluir não apaga conteúdo.** Os dois motivos de uma `Ocorrencia` existir
   (o usuário tocou / a data tem conteúdo) convivem na MESMA linha, por causa do
   `unique (evento, data)`. `_get_or_create_ocorrencia` tinha de continuar
   funcionando por cima de uma linha que já existe.
"""

import datetime

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from planner.models import Classe, Evento, Ocorrencia, RegraRecorrencia
from planner.services import agente, completion
from planner.services.recurrence import expandir

from .factories import (
    ClasseFactory,
    EventoFactory,
    OcorrenciaFactory,
    RegraRecorrenciaFactory,
    aware,
)

pytestmark = pytest.mark.django_db

# 2026-06-01 é uma segunda; a série cai em 01, 08 e 15 de junho.
SEG_1 = datetime.date(2026, 6, 1)
SEG_8 = datetime.date(2026, 6, 8)


@pytest.fixture
def api():
    return APIClient()


def _aula(**kwargs):
    """Uma disciplina como o semestre real a tem: série semanal, sem conclusão."""
    regra = RegraRecorrenciaFactory(tipo=RegraRecorrencia.Tipo.SEMANAL, dias=[0])
    kwargs.setdefault("rastrear_conclusao", False)
    return EventoFactory(
        titulo="Análise de Sistemas Lineares (ELEQ30)",
        descricao="Ementa da disciplina",
        inicio=aware(2026, 6, 1, 15, 50),
        fim=aware(2026, 6, 1, 17, 30),
        regra_recorrencia=regra,
        **kwargs,
    )


def _classe_prova(dono):
    """A classe `Prova` do dono — as 5 padrão já são semeadas com o perfil, então
    criar outra esbarraria em `uq_classe_dono_nome`."""
    classe, _ = Classe.objects.do_dono(dono).get_or_create(
        dono=dono, nome="Prova", defaults={"cor": "#fbeaea"}
    )
    return classe


def _expandir(ev):
    views = expandir(ev, aware(2026, 6, 1), aware(2026, 6, 20, 23, 59), set())
    return {v.data.isoformat(): v for v in views}


# --- 1. Resolução na leitura -------------------------------------------------


def test_sem_override_a_data_herda_a_serie():
    ev = _aula()
    por_data = _expandir(ev)
    assert por_data["2026-06-01"].titulo == "Análise de Sistemas Lineares (ELEQ30)"
    assert por_data["2026-06-01"].descricao == "Ementa da disciplina"
    assert por_data["2026-06-01"].classe == ev.classe


def test_conteudo_da_semana_entra_so_naquela_data():
    ev = _aula()
    OcorrenciaFactory(
        evento=ev, data=SEG_8, descricao_override="Transformada de Laplace Inversa"
    )
    por_data = _expandir(ev)
    assert por_data["2026-06-08"].descricao == "Transformada de Laplace Inversa"
    # O título continua o da disciplina: semana comum não sobrescreve (decisão D1).
    assert por_data["2026-06-08"].titulo == ev.titulo
    assert por_data["2026-06-01"].descricao == "Ementa da disciplina"


def test_dia_de_prova_troca_titulo_e_classe():
    ev = _aula()
    prova = _classe_prova(ev.dono)
    OcorrenciaFactory(
        evento=ev,
        data=SEG_8,
        titulo_override="ASL — Prova Teórica 1",
        descricao_override="Sinais, sistemas LIT e convolução",
        classe_override=prova,
    )
    por_data = _expandir(ev)
    assert por_data["2026-06-08"].titulo == "ASL — Prova Teórica 1"
    assert por_data["2026-06-08"].classe == prova
    # A cor do bloco vem da classe: as outras semanas seguem sendo Aula.
    assert por_data["2026-06-01"].classe == ev.classe
    assert por_data["2026-06-15"].classe == ev.classe


def test_override_nao_contamina_a_serie():
    ev = _aula()
    prova = _classe_prova(ev.dono)
    OcorrenciaFactory(
        evento=ev, data=SEG_8, titulo_override="Prova 1", classe_override=prova
    )
    ev.refresh_from_db()
    assert ev.titulo == "Análise de Sistemas Lineares (ELEQ30)"
    assert ev.classe != prova


def test_string_vazia_herda_em_vez_de_apagar():
    ev = _aula()
    OcorrenciaFactory(evento=ev, data=SEG_8, titulo_override="", descricao_override="")
    por_data = _expandir(ev)
    assert por_data["2026-06-08"].titulo == ev.titulo
    assert por_data["2026-06-08"].descricao == ev.descricao


def test_pulado_vence_o_conteudo():
    """Semana sem aula some do calendário mesmo com conteúdo preenchido."""
    ev = _aula()
    OcorrenciaFactory(
        evento=ev,
        data=SEG_8,
        descricao_override="(recesso — não haverá aula)",
        status_override="PULADO",
    )
    assert "2026-06-08" not in _expandir(ev)


# --- 2. O payload da API -----------------------------------------------------


def _dia(payload, data):
    return next(p for p in payload if p["ocorrencia"]["data"] == data)


def test_api_devolve_o_conteudo_do_dia_nos_campos_de_sempre(api):
    ev = _aula()
    prova = _classe_prova(ev.dono)
    OcorrenciaFactory(
        evento=ev,
        data=SEG_8,
        titulo_override="ASL — Prova Teórica 1",
        descricao_override="Sinais e convolução",
        classe_override=prova,
    )
    resp = api.get(
        "/api/v1/eventos/?inicio=2026-06-01T00:00:00-03:00"
        "&fim=2026-06-20T00:00:00-03:00"
    )
    assert resp.status_code == 200
    payload = resp.json()

    dia_prova = _dia(payload, "2026-06-08")
    # Campos de sempre: o frontend não sabe que override existe, e a cor sai
    # certa porque `classe` já vem trocada.
    assert dia_prova["titulo"] == "ASL — Prova Teórica 1"
    assert dia_prova["descricao"] == "Sinais e convolução"
    assert dia_prova["classe"]["nome"] == "Prova"
    assert dia_prova["classe"]["cor"] == "#fbeaea"

    dia_comum = _dia(payload, "2026-06-01")
    assert dia_comum["titulo"] == ev.titulo
    assert dia_comum["classe"]["id"] == str(ev.classe_id)


def test_api_evento_avulso_nao_muda(api):
    """Evento sem recorrência não passa por `_payload_ocorrencia` — e continua
    respondendo `ocorrencia: null`, como o contrato já dizia."""
    ev = EventoFactory(inicio=aware(2026, 6, 2, 9), fim=aware(2026, 6, 2, 10))
    resp = api.get(
        "/api/v1/eventos/?inicio=2026-06-01T00:00:00-03:00"
        "&fim=2026-06-20T00:00:00-03:00"
    )
    corpo = [p for p in resp.json() if p["id"] == str(ev.id)]
    assert corpo == [c for c in corpo if c["ocorrencia"] is None]
    assert corpo[0]["titulo"] == ev.titulo


# --- 3. O agente narra o dia, não a série ------------------------------------


def test_agente_diz_prova_no_dia_da_prova():
    ev = _aula()
    prova = _classe_prova(ev.dono)
    OcorrenciaFactory(
        evento=ev, data=SEG_8, titulo_override="ASL — Prova 1", classe_override=prova
    )
    dias = agente._consultar_agenda(ev.dono, "2026-06-01", "2026-06-20")
    por_data = {d["data"]: d["eventos"] for d in dias}
    assert por_data["2026-06-08"][0]["titulo"] == "ASL — Prova 1"
    assert por_data["2026-06-08"][0]["classe"] == "Prova"
    assert por_data["2026-06-01"][0]["classe"] == ev.classe.nome


# --- 4. Convivência com concluir/remarcar ------------------------------------


def test_concluir_ocorrencia_com_conteudo_nao_apaga_o_conteudo():
    ev = _aula(rastrear_conclusao=True, status=Evento.Status.AGENDADO)
    OcorrenciaFactory(evento=ev, data=SEG_8, descricao_override="Aula 3 — convolução")
    completion.concluir(ev, escopo="ocorrencia", data=SEG_8)
    oc = Ocorrencia.objects.get(evento=ev, data=SEG_8)
    assert oc.status_override == Evento.Status.CONCLUIDO
    assert oc.descricao_override == "Aula 3 — convolução"


def test_conteudo_em_data_ja_concluida_reusa_a_mesma_linha():
    """Ordem inversa: o usuário concluiu antes de o conteúdo ser importado.
    `unique (evento, data)` obriga a ser a mesma linha — importar não pode
    estourar constraint nem perder a conclusão."""
    ev = _aula(rastrear_conclusao=True, status=Evento.Status.AGENDADO)
    completion.concluir(ev, escopo="ocorrencia", data=SEG_8)
    oc = Ocorrencia.objects.get(evento=ev, data=SEG_8)
    oc.descricao_override = "Aula 3 — convolução"
    oc.save(update_fields=["descricao_override", "atualizado_em"])
    por_data = _expandir(ev)
    assert por_data["2026-06-08"].status == Evento.Status.CONCLUIDO
    assert por_data["2026-06-08"].descricao == "Aula 3 — convolução"
    assert Ocorrencia.objects.filter(evento=ev, data=SEG_8).count() == 1


# --- 5. Isolamento -----------------------------------------------------------


def test_classe_override_de_outro_dono_e_recusada(perfil, outro_perfil):
    """`Ocorrencia` não tem `dono` para o manager escopar, e `classe_override` é
    a primeira FK daqui para um model por-dono. Sem esta checagem, seria por onde
    a classe de um perfil apareceria no calendário de outro."""
    ev = _aula(dono=perfil)
    alheia = ClasseFactory(dono=outro_perfil, nome="Prova alheia")
    oc = Ocorrencia(evento=ev, data=SEG_8, classe_override=alheia)
    with pytest.raises(ValidationError) as exc:
        oc.full_clean()
    assert "classe_override" in exc.value.message_dict


def test_classe_override_do_mesmo_dono_passa(perfil):
    ev = _aula(dono=perfil)
    minha = _classe_prova(perfil)
    Ocorrencia(evento=ev, data=SEG_8, classe_override=minha).full_clean()


# --- 6. O solver não muda ----------------------------------------------------


def test_dia_de_prova_continua_ocupando_o_horario():
    """A classe do bloco nunca entrou na conta do solver, e não passa a entrar:
    trocar a classe do dia não pode liberar o horário da aula."""
    from planner.services.planejamento import intervalos_ocupados

    ev = _aula()
    prova = _classe_prova(ev.dono)
    OcorrenciaFactory(evento=ev, data=SEG_8, classe_override=prova)
    ocupados = intervalos_ocupados(ev.dono, aware(2026, 6, 1), aware(2026, 6, 20))
    assert (aware(2026, 6, 8, 15, 50), aware(2026, 6, 8, 17, 30)) in ocupados
