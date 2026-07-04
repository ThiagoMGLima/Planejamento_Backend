"""Derivação de PENDENTE e transições de estado (Handoff §5).

PENDENTE é calculado na leitura, nunca gravado. `concluir`/`remarcar` são as
únicas transições de escrita; remarcar devolve a Tarefa de origem ao Inbox.
"""

from django.db import transaction
from django.utils import timezone

from ..models import Evento, Ocorrencia, RegistroExecucao, Tarefa

PENDENTE = "PENDENTE"
PULADO = "PULADO"


def status_efetivo(item, agora=None):
    """Estado efetivo de um Evento ou de uma ocorrência expandida (§5.1).

    `item` precisa expor `rastrear_conclusao`, `status` e `fim` — vale tanto
    para o model Evento quanto para a OcorrenciaView de recurrence.expandir.
    """
    if not item.rastrear_conclusao:
        return None
    if item.status in (Evento.Status.CONCLUIDO, Evento.Status.REMARCADO):
        return item.status
    agora = agora or timezone.now()
    if agora > item.fim:
        return PENDENTE
    return Evento.Status.AGENDADO


def _get_or_create_ocorrencia(evento, data):
    ocorrencia, _ = Ocorrencia.objects.get_or_create(evento=evento, data=data)
    return ocorrencia


def _reabrir_ou_recriar_tarefa(evento):
    """Devolve a Tarefa de origem ao Inbox; recria se foi apagada (§5.2)."""
    tarefa = evento.origem_tarefa
    if tarefa is not None:
        tarefa.status = Tarefa.Status.INBOX
        tarefa.save(update_fields=["status", "atualizado_em"])
        return tarefa

    esforco = int((evento.fim - evento.inicio).total_seconds() // 60)
    return Tarefa.objects.create(
        titulo=evento.titulo,
        descricao=evento.descricao,
        classe=evento.classe,
        esforco_estimado=esforco,
        status=Tarefa.Status.INBOX,
    )


def _registrar_execucao(evento, remarcado, real_min=None):
    """Grava o histórico cru (Marco C3) — insumo dos fatores adaptativos."""
    RegistroExecucao.objects.create(
        tarefa=evento.origem_tarefa,
        evento=evento,
        classe=evento.classe,
        planejado_min=int((evento.fim - evento.inicio).total_seconds() // 60),
        real_min=real_min,
        remarcado=remarcado,
        concluido_em=None if remarcado else timezone.now(),
    )


@transaction.atomic
def concluir(evento, escopo="serie", data=None, real_min=None):
    """Marca CONCLUIDO. Em ocorrência, grava status_override = CONCLUIDO.

    `real_min` (opcional, informado pelo usuário) alimenta o fator de
    estimativa por classe; sem ele o registro ainda vale p/ flexibilidade.
    """
    _registrar_execucao(evento, remarcado=False, real_min=real_min)
    if escopo == "ocorrencia":
        ocorrencia = _get_or_create_ocorrencia(evento, data)
        ocorrencia.status_override = Evento.Status.CONCLUIDO
        ocorrencia.save(update_fields=["status_override", "atualizado_em"])
        return ocorrencia

    evento.status = Evento.Status.CONCLUIDO
    evento.save(update_fields=["status", "atualizado_em"])
    return evento


@transaction.atomic
def remarcar(evento, escopo="serie", data=None):
    """Marca REMARCADO e devolve a Tarefa de origem ao Inbox (§5.2).

    Remarcar não abre seletor de horário: encerra a ocorrência/série e reabre o
    Inbox para reentrar no ritual semanal.
    """
    _registrar_execucao(evento, remarcado=True)
    if escopo == "ocorrencia":
        ocorrencia = _get_or_create_ocorrencia(evento, data)
        ocorrencia.status_override = Evento.Status.REMARCADO
        ocorrencia.save(update_fields=["status_override", "atualizado_em"])
        tarefa = _reabrir_ou_recriar_tarefa(evento)
        return ocorrencia, tarefa

    evento.status = Evento.Status.REMARCADO
    evento.save(update_fields=["status", "atualizado_em"])
    tarefa = _reabrir_ou_recriar_tarefa(evento)
    return evento, tarefa
