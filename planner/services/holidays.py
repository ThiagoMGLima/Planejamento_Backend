"""Feriados nacionais via BrasilAPI, com cache e degradação graciosa (§7).

Buscar no servidor (nunca no navegador): evita CORS, rate limit e divergência.
Cache agressivo (30 dias) + cópia stale de longa duração para sobreviver a
falhas da API externa.
"""

import logging
from datetime import date

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

URL = "https://brasilapi.com.br/api/feriados/v1/{ano}"
TTL_FRESCO = 60 * 60 * 24 * 30  # 30 dias
TTL_STALE = 60 * 60 * 24 * 365  # 1 ano (fallback)


def feriados_do_ano(ano: int) -> set[date]:
    chave = f"feriados:{ano}"
    chave_stale = f"feriados:{ano}:stale"

    cached = cache.get(chave)
    if cached is not None:
        return cached

    try:
        resp = requests.get(URL.format(ano=ano), timeout=5)
        resp.raise_for_status()
        datas = {date.fromisoformat(item["date"]) for item in resp.json()}
        cache.set(chave, datas, TTL_FRESCO)
        cache.set(chave_stale, datas, TTL_STALE)
        return datas
    except Exception:
        # Degradação graciosa: nunca derrubar o request do calendário (§7).
        logger.warning(
            "Falha ao buscar feriados de %s na BrasilAPI", ano, exc_info=True
        )
        stale = cache.get(chave_stale)
        return stale if stale is not None else set()
