"""Tasks Celery do planner.

`planejar_ia_task` roda o pipeline de planejamento assistido por IA (Fase A):
solver monta o plano base → IA emite diretrizes → solver re-roda → alertas (código).
Cache no Redis por (tarefa_ids + prefs + plano base): entrada idêntica não chama
o Ollama de novo. Degrada para o plano base se a IA falhar/estiver desligada.

Importa só de `services` (não de `views`) para evitar import circular.
"""

import hashlib
import json
import time

from celery import shared_task
from celery.result import AsyncResult
from django.conf import settings
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from .services import adaptacao, cenarios, planejamento, planejamento_ia, tempos


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
    inicio = time.monotonic()
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
        # Só jobs em que a IA rodou calibram a estimativa (degradação
        # instantânea afundaria a razão real/prevista).
        tempos.registrar(
            "planejar_ia",
            time.monotonic() - inicio,
            planejamento_ia.estimar_tempo_s(base),
        )
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
    inicio = time.monotonic()
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

    if not ia_indisponivel:
        # Calibra a estimativa com a duração real (mesma prevista da view).
        tempos.registrar(
            "cenarios",
            time.monotonic() - inicio,
            tempos.FATOR_CENARIOS * planejamento_ia.estimar_tempo_s(base),
        )

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
        # Entrada original do lote: o refino (C5) reconstrói o plano base a
        # partir dela — sem isso o lote não é refinável.
        "entrada": {
            "tarefa_ids": [str(t) for t in tarefa_ids],
            "a_partir_de": a_partir_de_iso,
            "preferencias": preferencias,
            "horizonte_dias": horizonte_dias,
        },
    }

    cache.set(chave, resultado, timeout=3600)
    # Chave por job: o `escolher` recupera o lote pelo job_id mesmo após o
    # backend de resultado do Celery expirar.
    cache.set(f"cenarios_job:{job_id}", resultado, timeout=3600)
    return resultado


# Turnos de conversa reenviados ao modelo por lote (user+assistant = 2 por
# refino ⇒ 6 refinos de memória; acima disso o contexto do 7B só atrapalha).
MAX_MENSAGENS_CONVERSA = 12


@shared_task(bind=True)
def refinar_cenario_task(self, job_id, cenario_id, mensagem):
    """Refino conversacional de um lote de cenários (Marco C5).

    lote (cache) → reconstrói o plano base da `entrada` → IA traduz o pedido
    em diretrizes (única fonte de linguagem natural) → guarda-corpo → solver →
    métricas vs o MESMO base → cenário novo anexado ao lote (o `escolher`
    continua funcionando pelo job_id original). Ollama fora ⇒
    `ia_indisponivel: true`, lote intocado. A conversa do lote fica no cache
    (`cenarios_conversa:{job_id}`) e é reenviada nas chamadas seguintes.
    """
    inicio = time.monotonic()
    resultado = cache.get(f"cenarios_job:{job_id}")
    if resultado is None:
        job = AsyncResult(str(job_id))
        resultado = job.result if job.successful() else None
    if not resultado or "cenarios" not in resultado:
        raise ValueError("lote de cenários desconhecido ou expirado")
    entrada = resultado.get("entrada")
    if not entrada:
        raise ValueError("lote sem dados de entrada; gere os cenários novamente")

    agora = parse_datetime(entrada["a_partir_de"])
    validas, _ = planejamento.validar_tarefas(entrada["tarefa_ids"])
    base = planejamento.montar_plano(
        validas,
        agora,
        entrada["preferencias"],
        horizonte_dias=entrada["horizonte_dias"],
    )
    metricas_base = cenarios.metricas_do_plano(base)

    # Lote ENXUTO no contexto: só o cenário EM FOCO leva diretrizes; os demais
    # entram como id/nome/intencao (sem diretrizes, métricas ou trade-offs).
    # Testado no 7B: diretrizes numéricas dos outros cenários ancoram o modelo
    # a imitá-las e ele ignora `excluir_tarefas`. As métricas o código
    # recalcula depois — o modelo não precisa delas.
    foco = cenario_id or next(
        (c["id"] for c in resultado["cenarios"] if c.get("sugerido")), "base"
    )
    contexto = {
        **planejamento_ia.construir_contexto(base),
        "cenarios": [
            {
                "id": c["id"],
                "nome": c["nome"],
                "intencao": c["intencao"],
                **({"diretrizes": c["diretrizes"]} if c["id"] == foco else {}),
            }
            for c in resultado["cenarios"]
        ],
        "cenario_em_foco": foco,
    }
    chave_conversa = f"cenarios_conversa:{job_id}"
    historico = cache.get(chave_conversa) or []

    refino_id = self.request.id or "eager"
    try:
        if not settings.IA_PLANEJAMENTO_ENABLED:
            raise planejamento_ia.OllamaIndisponivel("IA desligada")
        bruto = cenarios.refinar_cenario_ia(contexto, historico, mensagem)
    except planejamento_ia.OllamaIndisponivel:
        refino = {
            "resposta": "",
            "cenario": None,
            "cenarios": resultado["cenarios"],
            "ia_indisponivel": True,
        }
        cache.set(f"cenarios_refino:{refino_id}", refino, timeout=3600)
        return refino

    diretrizes = planejamento_ia.validar_diretrizes(
        bruto.get("diretrizes"), base.tarefas, agora, base.horizonte_fim
    )
    res = planejamento.montar_plano(
        validas,
        agora,
        entrada["preferencias"],
        diretrizes,
        horizonte_dias=entrada["horizonte_dias"],
    )
    metricas = cenarios.metricas_do_plano(res)
    vs_base = cenarios.normalizar(metricas, metricas_base)
    pesos = resultado.get("pesos_usados") or {}

    origem = (
        cenario_id
        if any(c["id"] == cenario_id for c in resultado["cenarios"])
        else None
    )
    nome = str(bruto.get("nome") or "").strip() or "cenário ajustado"
    ids_usados = {c["id"] for c in resultado["cenarios"]}
    novo = {
        "id": cenarios.slug_cenario(nome, ids_usados),
        "nome": nome,
        "intencao": str(bruto.get("intencao") or ""),
        "origem": origem,
        "sugerido": False,
        "score": round(
            sum(pesos.get(m, 1.0) * vs_base[m] for m in cenarios.METRICAS), 4
        ),
        "diretrizes": diretrizes,
        "plano": planejamento.serializar_plano(res),
        "metricas": metricas,
        "metricas_vs_base": vs_base,
        "trade_offs": cenarios.narrar({"metricas": metricas}, metricas_base),
        "alertas": planejamento_ia.alertas_do_plano(res),
    }

    resultado["cenarios"] = [*resultado["cenarios"], novo]
    cache.set(f"cenarios_job:{job_id}", resultado, timeout=3600)

    # Calibra a estimativa com a duração real (mesma prevista da view).
    tempos.registrar(
        "refino", time.monotonic() - inicio, planejamento_ia.estimar_tempo_s(base)
    )

    resposta = str(bruto.get("resposta") or "")
    historico = (
        historico
        + [
            {"role": "user", "content": mensagem},
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "resposta": resposta,
                        "nome": nome,
                        "intencao": novo["intencao"],
                        "diretrizes": diretrizes,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    )[-MAX_MENSAGENS_CONVERSA:]
    cache.set(chave_conversa, historico, timeout=3600)

    refino = {
        "resposta": resposta,
        "cenario": novo,
        "cenarios": resultado["cenarios"],
        "ia_indisponivel": False,
    }
    cache.set(f"cenarios_refino:{refino_id}", refino, timeout=3600)
    return refino
