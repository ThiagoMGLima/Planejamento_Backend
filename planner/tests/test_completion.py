"""Testes de services/completion: fronteiras de status_efetivo e transições."""

import pytest

from planner.models import Evento, Tarefa
from planner.services import completion

from .factories import ClasseFactory, EventoFactory, TarefaFactory, aware

pytestmark = pytest.mark.django_db


def test_nao_rastreavel_retorna_none():
    ev = EventoFactory(rastrear_conclusao=False)
    assert completion.status_efetivo(ev, agora=aware(2026, 7, 1)) is None


def test_agendado_antes_do_fim():
    ev = EventoFactory(
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        fim=aware(2026, 6, 1, 10),
    )
    assert (
        completion.status_efetivo(ev, agora=aware(2026, 6, 1, 9))
        == Evento.Status.AGENDADO
    )


def test_fronteira_exata_nao_e_pendente():
    # agora == fim → não é "agora > fim" → segue AGENDADO.
    ev = EventoFactory(
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        fim=aware(2026, 6, 1, 10),
    )
    assert (
        completion.status_efetivo(ev, agora=aware(2026, 6, 1, 10))
        == Evento.Status.AGENDADO
    )


def test_pendente_apos_o_fim():
    ev = EventoFactory(
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        fim=aware(2026, 6, 1, 10),
    )
    assert completion.status_efetivo(ev, agora=aware(2026, 6, 1, 11)) == "PENDENTE"


def test_concluido_tem_precedencia_sobre_pendente():
    ev = EventoFactory(
        rastrear_conclusao=True,
        status=Evento.Status.CONCLUIDO,
        fim=aware(2026, 6, 1, 10),
    )
    assert (
        completion.status_efetivo(ev, agora=aware(2026, 7, 1))
        == Evento.Status.CONCLUIDO
    )


def test_concluir_serie():
    ev = EventoFactory(rastrear_conclusao=True, status=Evento.Status.AGENDADO)
    completion.concluir(ev, escopo="serie")
    ev.refresh_from_db()
    assert ev.status == Evento.Status.CONCLUIDO


def test_concluir_ocorrencia_grava_override():
    ev = EventoFactory(rastrear_conclusao=True, status=Evento.Status.AGENDADO)
    completion.concluir(ev, escopo="ocorrencia", data=aware(2026, 6, 8).date())
    oc = ev.ocorrencias.get(data=aware(2026, 6, 8).date())
    assert oc.status_override == Evento.Status.CONCLUIDO


def test_remarcar_serie_reabre_tarefa_de_origem():
    tarefa = TarefaFactory(status=Tarefa.Status.PROMOVIDA)
    ev = EventoFactory(
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        origem_tarefa=tarefa,
    )
    completion.remarcar(ev, escopo="serie")
    ev.refresh_from_db()
    tarefa.refresh_from_db()
    assert ev.status == Evento.Status.REMARCADO
    assert tarefa.status == Tarefa.Status.INBOX


def test_remarcar_recria_tarefa_quando_origem_ausente():
    classe = ClasseFactory()
    ev = EventoFactory(
        classe=classe,
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        origem_tarefa=None,
        inicio=aware(2026, 6, 1, 8),
        fim=aware(2026, 6, 1, 9, 30),
    )
    _, tarefa = completion.remarcar(ev, escopo="serie")
    assert tarefa.status == Tarefa.Status.INBOX
    assert tarefa.classe == classe
    assert tarefa.esforco_estimado == 90  # 1h30 → 90 min
