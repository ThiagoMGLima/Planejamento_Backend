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

from .services import adaptacao, cenarios, planejamento, planejamento_ia


def _chave_cache(tarefa_ids, prefs_usadas, sessoes_base, prefixo="planejar_ia"):
    """Chave determinística do resultado: ids + prefs efetivas + plano base.

    O plano base já é função determinística das entradas; usá-lo na chave garante
    que mudanças relevantes invalidem o cache. `prefixo` separa as famílias de
    chave (planejar-ia vs cenários).
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
    return f"{prefixo}:" + hashlib.sha256(base.encode()).hexdigest()


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


@shared_task(bind=True)
def gerar_cenarios_task(
    self, tarefa_ids, a_partir_de_iso, preferencias, horizonte_dias=None
):
    """Pipeline de cenários (Marco C1b, §2.2 da visão).

    plano base → contexto → arquétipos (código) + IA (1 chamada) → guarda-corpo
    → solver N× (ms cada) → métricas → dominância → score/pesos → top 4 com
    narrativa. Ollama fora ⇒ só os arquétipos + `ia_indisponivel: true`.
    """
    agora = parse_datetime(a_partir_de_iso)
    validas, _ = planejamento.validar_tarefas(tarefa_ids)
    base = planejamento.montar_plano(
        validas, agora, preferencias, horizonte_dias=horizonte_dias
    )
    plano_base = planejamento.serializar_plano(base)

    chave = _chave_cache(
        tarefa_ids, plano_base["preferencias_usadas"], plano_base["sessoes"], "cenarios"
    )
    job_id = self.request.id or "eager"
    hit = cache.get(chave)
    if hit is not None:
        cache.set(f"cenarios_job:{job_id}", hit, timeout=3600)
        return hit

    contexto = planejamento_ia.construir_contexto(base)

    # Candidatos: arquétipos por código (sempre) + IA (personalizados; degrada).
    candidatos = [
        {
            "nome": nome,
            "intencao": cenarios.INTENCOES_ARQUETIPOS[nome],
            "diretrizes": fn(contexto),
        }
        for nome, fn in cenarios.ARQUETIPOS.items()
    ]
    ia_indisponivel = False
    try:
        if not settings.IA_PLANEJAMENTO_ENABLED:
            raise planejamento_ia.OllamaIndisponivel("IA desligada")
        candidatos += cenarios.gerar_cenarios_ia(contexto)
    except planejamento_ia.OllamaIndisponivel:
        ia_indisponivel = True

    # Guarda-corpo + solver + métricas para TODOS (arquétipos inclusive).
    metricas_base = cenarios.metricas_do_plano(base)
    lote = []
    ids_usados = {"base"}  # reservado ao arquétipo de referência
    for cand in candidatos:
        if not isinstance(cand, dict):
            continue
        nome = str(cand.get("nome") or "").strip() or "cenário"
        cid = "base" if nome == "base" else cenarios.slug_cenario(nome, ids_usados)
        diretrizes = planejamento_ia.validar_diretrizes(
            cand.get("diretrizes"), base.tarefas, agora, base.horizonte_fim
        )
        if cid == "base":
            res = base  # diretrizes vazias por construção; não recalcula
        else:
            res = planejamento.montar_plano(
                validas, agora, preferencias, diretrizes, horizonte_dias=horizonte_dias
            )
        metricas = cenarios.metricas_do_plano(res)
        lote.append(
            {
                "id": cid,
                "nome": nome,
                "intencao": str(cand.get("intencao") or ""),
                "diretrizes": diretrizes,
                "res": res,
                "metricas": metricas,
                "metricas_vs_base": cenarios.normalizar(metricas, metricas_base),
            }
        )

    # Decaimento "ao ler" (C3): pesos antigos escorregam 2% rumo ao neutro a
    # cada lote gerado — gostos mudam com o semestre.
    pesos = adaptacao.decair_pesos()
    finalistas = cenarios.pontuar(cenarios.filtrar_dominados(lote), pesos)

    resultado = {
        "cenarios": [
            {
                "id": c["id"],
                "nome": c["nome"],
                "intencao": c["intencao"],
                "sugerido": c["sugerido"],
                "score": c["score"],
                "diretrizes": c["diretrizes"],
                "plano": planejamento.serializar_plano(c["res"]),
                "metricas": c["metricas"],
                "metricas_vs_base": c["metricas_vs_base"],
                "trade_offs": cenarios.narrar(c, metricas_base),
                "alertas": planejamento_ia.alertas_do_plano(c["res"]),
            }
            for c in finalistas
        ],
        "pesos_usados": pesos,
        "ia_indisponivel": ia_indisponivel,
    }

    cache.set(chave, resultado, timeout=3600)
    # Chave por job: o `escolher` recupera o lote pelo job_id mesmo após o
    # backend de resultado do Celery expirar.
    cache.set(f"cenarios_job:{job_id}", resultado, timeout=3600)
    return resultado
