"""Tasks Celery do planner.

`planejar_ia_task` roda o pipeline de planejamento assistido por IA (Fase A):
solver monta o plano base → IA emite diretrizes → solver re-roda → alertas (código).
Cache no Redis por (tarefa_ids + prefs + plano base): entrada idêntica não chama
o Ollama de novo. Degrada para o plano base se a IA falhar/estiver desligada.

Importa só de `services` (não de `views`) para evitar import circular.
"""

import hashlib
import json

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from .services import planejamento, planejamento_ia


def _chave_cache(tarefa_ids, prefs_usadas, sessoes_base):
    """Chave determinística do resultado: ids + prefs efetivas + plano base.

    O plano base já é função determinística das entradas; usá-lo na chave garante
    que mudanças relevantes invalidem o cache.
    """
    base = json.dumps(
        {
            "ids": sorted(map(str, tarefa_ids)),
            "prefs": prefs_usadas,
            "plano": [(s["tarefa_id"], s["inicio"], s["fim"]) for s in sessoes_base],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "planejar_ia:" + hashlib.sha256(base.encode()).hexdigest()


@shared_task
def planejar_ia_task(tarefa_ids, a_partir_de_iso, preferencias, horizonte_dias=None):
    """Pipeline assíncrono. Retorna o dict do contrato (ver docs/tasks)."""
    agora = parse_datetime(a_partir_de_iso)
    validas, _ = planejamento.validar_tarefas(tarefa_ids)
    base = planejamento.montar_plano(
        validas, agora, preferencias, horizonte_dias=horizonte_dias
    )
    plano_base = planejamento.serializar_plano(base)

    chave = _chave_cache(
        tarefa_ids, plano_base["preferencias_usadas"], plano_base["sessoes"]
    )
    hit = cache.get(chave)
    if hit is not None:
        return hit

    try:
        if not settings.IA_PLANEJAMENTO_ENABLED:
            raise planejamento_ia.OllamaIndisponivel("IA desligada")
        contexto = planejamento_ia.construir_contexto(base)
        bruto = planejamento_ia.gerar_melhoria(contexto)
        diretrizes = planejamento_ia.validar_diretrizes(
            bruto.get("diretrizes", {}), base.tarefas, base.agora, base.horizonte_fim
        )
        melhor = planejamento.montar_plano(
            validas, agora, preferencias, diretrizes, horizonte_dias=horizonte_dias
        )
        resultado = {
            "plano": planejamento.serializar_plano(melhor),
            "resumo": bruto.get("resumo", ""),
            "trade_offs": bruto.get("trade_offs", []),
            "alertas": planejamento_ia.alertas_do_plano(melhor),
            "sugestoes": bruto.get("sugestoes", []),
            "ia_indisponivel": False,
        }
    except planejamento_ia.OllamaIndisponivel:
        resultado = {
            "plano": plano_base,
            "resumo": "",
            "trade_offs": [],
            "alertas": planejamento_ia.alertas_do_plano(base),
            "sugestoes": [],
            "ia_indisponivel": True,
        }

    cache.set(chave, resultado, timeout=3600)
    return resultado
