"""Feriados: nacional (BrasilAPI) ∪ estadual (lib offline) ∪ municipal (DB).

Marco C8 — este módulo é o PONTO ÚNICO de merge: `feriados_do_ano` alimenta a
recorrência (`ignorar_feriados`), o solver e o endpoint GET /feriados, então
tudo passa a enxergar as três camadas sem mudar os consumidores.

- Nacional: BrasilAPI no servidor (nunca no navegador — CORS/rate limit), com
  cache agressivo (30 dias) + cópia stale de longa duração para sobreviver a
  falhas da API externa (§7). Já inclui os móveis (Carnaval, Corpus Christi).
- Estadual: biblioteca `holidays` (offline, sem token, cobre as 27 UFs) via
  FERIADOS_UF. Nota: o Paraná não tem feriado estadual oficial — para
  Curitiba a camada que age é a municipal.
- Municipal: model `FeriadoLocal` (manual, editável no admin — não há API
  confiável para os 5570 municípios). Lido do DB a cada chamada, de propósito:
  edições no admin valem na hora, sem esperar cache expirar.

Cada camada degrada sozinha para conjunto vazio; uma falha nunca derruba o
request do calendário.
"""

import logging
from datetime import date

import holidays as holidays_lib
import requests
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

logger = logging.getLogger(__name__)

URL = "https://brasilapi.com.br/api/feriados/v1/{ano}"
TTL_FRESCO = 60 * 60 * 24 * 30  # 30 dias
TTL_STALE = 60 * 60 * 24 * 365  # 1 ano (fallback)


def feriados_do_ano(ano: int) -> set[date]:
    return _nacionais(ano) | _estaduais(ano) | _municipais(ano)


def _nacionais(ano: int) -> set[date]:
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


def _estaduais(ano: int) -> set[date]:
    """Feriados da UF configurada (FERIADOS_UF), offline via lib `holidays`.

    A lib devolve nacional ∪ estadual da UF — o excedente nacional é inócuo no
    merge (união de conjuntos) e ainda serve de rede extra se a BrasilAPI cair.
    """
    uf = settings.FERIADOS_UF
    if not uf:
        return set()
    try:
        return set(holidays_lib.country_holidays("BR", subdiv=uf, years=ano))
    except Exception:
        logger.warning("FERIADOS_UF inválida ou lib falhou: %r", uf, exc_info=True)
        return set()


def _municipais(ano: int) -> set[date]:
    """Feriados locais do DB: recorrentes (ano nulo) + pontuais do ano."""
    from planner.models import FeriadoLocal  # import local: evita ciclo no boot

    datas = set()
    for f in FeriadoLocal.objects.filter(Q(ano__isnull=True) | Q(ano=ano)):
        try:
            datas.add(date(ano, f.mes, f.dia))
        except ValueError:  # ex.: 29/02 em ano não bissexto — pula neste ano
            continue
    return datas
