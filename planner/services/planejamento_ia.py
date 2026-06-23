"""Planejamento assistido por IA (Fase A) — contexto, chamada e guarda-corpos.

A IA (Ollama, local) **não** monta nem posiciona o plano: ela só emite
**diretrizes** de alto nível (prioridades e ajustes por tarefa) que o solver
re-roda — o plano melhorado é válido por construção. Os números concretos
(o que não coube, carga por dia) saem do **código**, não da IA (grounding).

Fluxo (ver docs/tasks/planejamento-ia-*.md):
    construir_contexto(res) → gerar_melhoria(contexto) → validar_diretrizes(...)
    → solver re-roda → alertas_do_plano(res_melhorado).

Tudo aqui degrada com segurança: se o Ollama falhar, `gerar_melhoria` levanta
`OllamaIndisponivel` e a task entrega o plano base.
"""

import json

import ollama
from django.conf import settings
from django.utils import timezone

from . import planejamento


class OllamaIndisponivel(Exception):
    """Ollama desligado/timeout/erro de rede ou resposta não-parseável."""


# --------------------------------------------------------------------------- #
# 1. Contexto grounded (fatos já calculados; a IA não recomputa)              #
# --------------------------------------------------------------------------- #
def _minutos_livres(agora, fim, pn, granularidade, ocupado):
    """Soma dos minutos livres em [agora, fim] sob as preferências `pn`."""
    if fim <= agora:
        return 0
    return sum(
        int((f - i).total_seconds() // 60)
        for i, f in planejamento.slots_livres(agora, fim, pn, granularidade, ocupado)
    )


def construir_contexto(res):
    """Monta o dict de FATOS para a IA a partir de um ResultadoPlano (base).

    Tudo aqui é grounded: vem do solver/banco, nunca da IA.
    """
    agora = res.agora
    tz = timezone.get_current_timezone()
    pn_base = planejamento._prefs_do_nivel(res.prefs, 0)

    alocado = {}
    sessoes_por_tarefa = {}
    dias_por_tarefa = {}
    carga_por_dia = {}
    for s in res.sessoes:
        dia = timezone.localtime(s.inicio, tz).date().isoformat()
        alocado[s.tarefa_id] = alocado.get(s.tarefa_id, 0) + s.dur_min
        sessoes_por_tarefa[s.tarefa_id] = sessoes_por_tarefa.get(s.tarefa_id, 0) + 1
        dias_por_tarefa.setdefault(s.tarefa_id, set()).add(dia)
        carga_por_dia[dia] = carga_por_dia.get(dia, 0) + s.dur_min

    restante = {n.tarefa_id: n.minutos_restantes for n in res.nao_alocado}

    tarefas = []
    capacidade_livre = {}
    for te in res.tarefas:
        fim = min(planejamento._deadline_efetiva(te, agora), res.horizonte_fim)
        capacidade_livre[te.id] = _minutos_livres(
            agora, fim, pn_base, res.prefs.granularidade, res.ocupado
        )
        tarefas.append(
            {
                "id": te.id,
                "titulo": te.titulo,
                "classe": te.classe_id,
                "deadline": te.deadline.isoformat(),
                "esforco_min": te.esforco,
                "alocado_min": alocado.get(te.id, 0),
                "restante_min": restante.get(te.id, 0),
                "sessoes": sessoes_por_tarefa.get(te.id, 0),
                "dias_usados": sorted(dias_por_tarefa.get(te.id, set())),
            }
        )

    cargas = list(carga_por_dia.values())
    carga_resumo = {
        "dias_com_carga": len(cargas),
        "carga_maxima_dia_min": max(cargas) if cargas else 0,
        "carga_media_dia_min": round(sum(cargas) / len(cargas)) if cargas else 0,
    }

    return {
        "agora": agora.isoformat(),
        "tarefas": tarefas,
        "carga_por_dia": dict(sorted(carga_por_dia.items())),
        "carga_resumo": carga_resumo,
        "capacidade_livre_antes_da_deadline": capacidade_livre,
        "nao_alocado": [
            {
                "id": n.tarefa_id,
                "titulo": n.tarefa_titulo,
                "restante_min": n.minutos_restantes,
                "motivo": n.motivo,
            }
            for n in res.nao_alocado
        ],
        "preferencias": res.prefs_usadas,
    }


# --------------------------------------------------------------------------- #
# 2. Chamada de IA (uma só) — diretrizes + explicação                         #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "Você melhora e explica planos de estudo, buscando uma rotina HUMANA e "
    "sustentável. Receberá FATOS já calculados e o plano atual. NUNCA invente "
    "números, horários ou datas — use apenas os FATOS. "
    "OBJETIVOS, nesta ordem de prioridade: (1) respeitar os prazos; "
    "(2) distribuir o esforço ao longo dos dias em vez de amontoar — prefira "
    "sessões menores e mais frequentes a poucos dias pesados; (3) suavizar os "
    "picos: deixar a carga dos dias mais parecida entre si, baixando os dias "
    "mais cheios; (4) manter alguma folga antes dos prazos. Use 'carga_por_dia' "
    "e 'carga_resumo' para enxergar os dias pesados e o pico a reduzir. "
    "ALAVANCAS (o id da tarefa entra SOMENTE como chave em 'diretrizes'): "
    "prioridades (1 a 5); por tarefa, buffer_dias (terminar com folga antes do "
    "prazo) e max_min_por_dia (reduza-o para espalhar AQUELA tarefa por mais "
    "dias); e max_min_por_dia_total (teto de minutos por dia somando TODAS as "
    "tarefas — use para derrubar os picos de carga diária). Só aperte os limites "
    "enquanto os prazos ainda couberem; o sistema afrouxa sozinho o que não couber. "
    "Por fim, explique de forma objetiva e factual a estratégia, os trade-offs e "
    "sugestões. Nos textos (resumo, trade_offs, sugestoes) refira-se às tarefas "
    "SOMENTE pelo título, NUNCA escreva o id/UUID, e NUNCA cite nomes técnicos de "
    "campos como 'buffer_dias', 'max_min_por_dia', 'max_min_por_dia_total' ou "
    "'prioridade' — descreva em linguagem natural (ex.: 'margem de N dias antes "
    "do prazo', 'limite diário por matéria', 'carga diária total', 'mais "
    "importante'). Sem linguagem floreada. Responda no schema JSON."
)

# JSON Schema da resposta (§4 do design). `format=` força JSON válido no Ollama.
SCHEMA_MELHORIA = {
    "type": "object",
    "properties": {
        "diretrizes": {
            "type": "object",
            "properties": {
                "prioridades": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "ajustes_por_tarefa": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "buffer_dias": {"type": "integer"},
                            "max_min_por_dia": {"type": "integer"},
                        },
                    },
                },
                "max_min_por_dia_total": {"type": "integer"},
            },
        },
        "resumo": {"type": "string"},
        "trade_offs": {"type": "array", "items": {"type": "string"}},
        "sugestoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string"},
                    "descricao": {"type": "string"},
                    "acao": {"type": "object"},
                },
            },
        },
    },
    "required": ["diretrizes", "resumo", "trade_offs", "sugestoes"],
}


def gerar_melhoria(contexto):
    """Uma chamada ao Ollama. Retorna o dict bruto (ainda a validar).

    Qualquer falha (rede, timeout, JSON inválido) vira `OllamaIndisponivel`.
    """
    try:
        cli = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT
        )
        resp = cli.chat(
            model=settings.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(contexto, ensure_ascii=False),
                },
            ],
            format=SCHEMA_MELHORIA,
            options={"temperature": 0},
        )
        return json.loads(resp["message"]["content"])
    except Exception as e:  # rede, timeout, JSON inválido, etc.
        raise OllamaIndisponivel(str(e))


# --------------------------------------------------------------------------- #
# 3. Guarda-corpo das diretrizes (descarta o inválido, nunca lança)           #
# --------------------------------------------------------------------------- #
def _como_int(valor):
    """Coerção tolerante p/ int (aceita 3, 3.0, "3"); None se não der."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def validar_diretrizes(bruto, tarefas_validas):
    """Limpa as diretrizes da IA contra as tarefas reais (ver §4.3).

    - `prioridades[id]`: id existente; inteiro com clamp 1..5.
    - `ajustes_por_tarefa[id]`: id existente; `buffer_dias` int ≥ 0
      (clamp ≤ horizonte) e `max_min_por_dia` int ≥ 1. Campos inválidos somem.
    - `max_min_por_dia_total`: teto diário global (todas as tarefas), int ≥ 1.
      Só entra no retorno quando válido (montar_plano o ignora se ausente).
    Chaves desconhecidas são removidas. Nunca levanta.
    """
    if not isinstance(bruto, dict):
        return {"prioridades": {}, "ajustes_por_tarefa": {}}

    ids = {te.id for te in tarefas_validas}
    max_buffer = planejamento.JANELA_MAX.days

    prioridades = {}
    for tid, valor in (bruto.get("prioridades") or {}).items():
        if tid not in ids:
            continue
        n = _como_int(valor)
        if n is None:
            continue
        prioridades[tid] = max(1, min(5, n))

    ajustes = {}
    for tid, aj in (bruto.get("ajustes_por_tarefa") or {}).items():
        if tid not in ids or not isinstance(aj, dict):
            continue
        limpo = {}
        buffer_dias = _como_int(aj.get("buffer_dias"))
        if buffer_dias is not None:
            limpo["buffer_dias"] = max(0, min(max_buffer, buffer_dias))
        max_dia = _como_int(aj.get("max_min_por_dia"))
        if max_dia is not None and max_dia >= 1:
            limpo["max_min_por_dia"] = max_dia
        if limpo:
            ajustes[tid] = limpo

    resultado = {"prioridades": prioridades, "ajustes_por_tarefa": ajustes}
    teto_total = _como_int(bruto.get("max_min_por_dia_total"))
    if teto_total is not None and teto_total >= 1:
        resultado["max_min_por_dia_total"] = teto_total
    return resultado


# --------------------------------------------------------------------------- #
# 3b. Estimativa de tempo (CÓDIGO, sem IA) — expectativa, não promessa         #
# --------------------------------------------------------------------------- #
def estimar_tempo_s(res):
    """Estimativa grosseira do tempo de geração pela IA, em segundos.

    Modelo linear `base + por_tarefa·n`, calibrado para o 7B *warm* no CPU. O
    driver é o nº de tarefas com sessão no escopo: horizonte menor aloca menos
    tarefas → estimativa menor. É expectativa para alinhar a espera do usuário,
    não SLA — cold start e contenção variam. Constantes em settings.
    """
    n = len({s.tarefa_id for s in res.sessoes})
    return settings.PLANEJAR_TEMPO_BASE_S + settings.PLANEJAR_TEMPO_POR_TAREFA_S * n


# --------------------------------------------------------------------------- #
# 4. Alertas concretos (CÓDIGO, sem IA) — grounded no plano realizado         #
# --------------------------------------------------------------------------- #
def alertas_do_plano(res):
    """Deriva alertas do plano (ResultadoPlano), sem tocar na IA.

    - cada item em `nao_alocado` → severidade "alto".
    - dia cuja carga ultrapassa o teto total (quando houver) → "medio". O teto
      pode ser estourado quando o relaxamento o zera para caber antes do prazo.
    """
    alertas = []
    for n in res.nao_alocado:
        alertas.append(
            {
                "tarefa_id": n.tarefa_id,
                "severidade": "alto",
                "mensagem": (
                    f"Faltam {n.minutos_restantes} min de "
                    f"{n.tarefa_titulo} antes do prazo."
                ),
            }
        )

    teto = res.prefs.max_min_por_dia_total
    if teto is not None:
        tz = timezone.get_current_timezone()
        carga = {}
        for s in res.sessoes:
            dia = timezone.localtime(s.inicio, tz).date()
            carga[dia] = carga.get(dia, 0) + s.dur_min
        for dia, total in sorted(carga.items()):
            if total > teto:
                alertas.append(
                    {
                        "tarefa_id": None,
                        "severidade": "medio",
                        "mensagem": (
                            f"Dia {dia.isoformat()} com {total} min planejados "
                            f"(acima do teto de {teto})."
                        ),
                    }
                )

    return alertas
