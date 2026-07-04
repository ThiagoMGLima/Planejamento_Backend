"""Persistência de um plano (sessões → Eventos) — o "aplicar".

Extraído da view `/planejamento/aplicar` (C1b) para ser reusado pelo
`/planejamento/cenarios/escolher`: as funções de plano continuam puras;
persistir é SEMPRE daqui.
"""

from django.db import transaction
from django.utils.dateparse import parse_datetime

from ..models import Evento, Tarefa


class AplicacaoInvalida(Exception):
    """Sessões não aplicáveis; `erros` já no shape de resposta 400 da API."""

    def __init__(self, erros):
        super().__init__(str(erros))
        self.erros = erros


def _normalizar_sessao(s):
    """Aceita dicts vindos do serializer (UUID/datetime) ou de cache/JSON (str)."""
    inicio, fim = s["inicio"], s["fim"]
    return {
        "tarefa_id": str(s["tarefa_id"]),
        "inicio": parse_datetime(inicio) if isinstance(inicio, str) else inicio,
        "fim": parse_datetime(fim) if isinstance(fim, str) else fim,
    }


def aplicar_sessoes(sessoes):
    """Cria um Evento por sessão (atômico) e marca as tarefas como PROMOVIDA.

    Retorna a lista de Eventos criados. Tarefa inexistente ou sem classe ⇒
    AplicacaoInvalida (nada é gravado).
    """
    sessoes = [_normalizar_sessao(s) for s in sessoes]
    ids = {s["tarefa_id"] for s in sessoes}
    por_id = {
        str(t.id): t for t in Tarefa.objects.select_related("classe").filter(id__in=ids)
    }

    faltando = sorted(tid for tid in ids if tid not in por_id)
    if faltando:
        raise AplicacaoInvalida(
            {"tarefa_id": [f"Tarefa(s) inexistente(s): {', '.join(faltando)}"]}
        )

    sem_classe = sorted(tid for tid in ids if por_id[tid].classe_id is None)
    if sem_classe:
        raise AplicacaoInvalida(
            {"classe_id": [f"Tarefa(s) sem classe: {', '.join(sem_classe)}"]}
        )

    with transaction.atomic():
        criados = [
            Evento.objects.create(
                titulo=por_id[s["tarefa_id"]].titulo,
                descricao=por_id[s["tarefa_id"]].descricao,
                inicio=s["inicio"],
                fim=s["fim"],
                classe=por_id[s["tarefa_id"]].classe,
                rastrear_conclusao=True,
                status=Evento.Status.AGENDADO,
                origem_tarefa=por_id[s["tarefa_id"]],
            )
            for s in sessoes
        ]
        for tarefa in por_id.values():
            if tarefa.status != Tarefa.Status.PROMOVIDA:
                tarefa.status = Tarefa.Status.PROMOVIDA
                tarefa.save(update_fields=["status", "atualizado_em"])

    return criados
