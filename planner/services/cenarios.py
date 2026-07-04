"""Cenários com trade-offs (Marco C1b) — arquétipos, métricas e pontuação.

Em vez de um único plano melhorado, o pipeline monta 3–4 CENÁRIOS comparáveis
("trabalhe até 20h na quinta e ganhe o sábado"), cada um com métricas medidas
pelo código e uma narrativa do trade-off. Generate-and-test: a IA só propõe
candidatos plausíveis (quais knobs mexer); TODOS são executados pelo solver e
medidos aqui — os ruins morrem no filtro de dominância antes de chegar ao
usuário. Números nunca vêm da IA.

Fluxo (na task gerar_cenarios_task):
    plano base → construir_contexto → ARQUETIPOS (código, sempre) +
    gerar_cenarios_ia (1 chamada, degrada) → validar_diretrizes → solver N× →
    metricas_do_plano → normalizar → filtrar_dominados → pontuar → narrar.
"""

import json
import re
import unicodedata
from datetime import date, timedelta

import ollama
from django.conf import settings
from django.utils import timezone

from .adaptacao import METRICAS  # noqa: F401 — dono das métricas: adaptacao
from .planejamento_ia import OllamaIndisponivel

# Nos custos (não-benefício), menor = melhor; a normalização inverte o sinal
# para que "maior = melhor" valha em todas (pesos comparáveis entre si).
METRICAS_BENEFICIO = frozenset({"dias_livres", "fds_livres", "folga_media_h"})

# Máximo de cenários exibidos ao usuário (base sempre incluso).
MAX_CENARIOS = 4


# --------------------------------------------------------------------------- #
# Arquétipos por CÓDIGO (sempre presentes; degradação sem IA)                  #
# --------------------------------------------------------------------------- #
def _minutos_para_hhmm(minutos):
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def _hhmm_para_minutos(valor):
    horas, mins = valor.split(":")
    return int(horas) * 60 + int(mins)


def _espalhado(ctx):
    """Teto diário total ≈ carga média real → espalha e derruba picos."""
    media = ctx["carga_resumo"]["carga_media_dia_min"]
    return {"max_min_por_dia_total": media} if media else {}


def _fins_de_semana(ctx):
    """Sábados e domingos entre agora e o horizonte (máx. 14, teto do guarda-corpo)."""
    ini = date.fromisoformat(ctx["agora"][:10])
    fim = date.fromisoformat(ctx["horizonte_fim"][:10])
    dias = []
    dia = ini
    while dia <= fim and len(dias) < 14:
        if dia.weekday() >= 5:
            dias.append(dia.isoformat())
        dia += timedelta(days=1)
    return dias


def _intenso(ctx):
    """Janela +2h seg–sex; fim de semana bloqueado (dias úteis concentram tudo)."""
    prefs = ctx["preferencias"]
    ini = prefs.get("janela_inicio", "08:00")
    fim_min = min(
        _hhmm_para_minutos(prefs.get("janela_fim", "22:00")) + 120, 23 * 60 + 59
    )
    janela = [ini, _minutos_para_hhmm(fim_min)]
    diretrizes = {"janela_por_dia": {str(d): janela for d in range(5)}}
    fds = _fins_de_semana(ctx)
    if fds:
        diretrizes["dias_bloqueados"] = fds
    return diretrizes


def _frente_carregada(ctx):
    """buffer_dias=2 nas 3 deadlines mais próximas → termina com folga."""
    tarefas = sorted(ctx["tarefas"], key=lambda t: t["deadline"])[:3]
    return {"ajustes_por_tarefa": {t["id"]: {"buffer_dias": 2} for t in tarefas}}


# Cada arquétipo é fn(contexto) -> dict de diretrizes, derivando valores dos
# FATOS (nada fixo). "base" é a referência de comparação, sempre presente.
ARQUETIPOS = {
    "base": lambda ctx: {},
    "espalhado": _espalhado,
    "intenso": _intenso,
    "frente_carregada": _frente_carregada,
}

INTENCOES_ARQUETIPOS = {
    "base": "O plano de referência, com as suas preferências como estão.",
    "espalhado": "Espalhar o esforço e derrubar os picos de carga diária.",
    "intenso": "Concentrar nos dias úteis (janela maior) e ganhar o fim de semana.",
    "frente_carregada": "Terminar as tarefas mais urgentes com folga antes do prazo.",
}


# --------------------------------------------------------------------------- #
# IA proponente (1 chamada, schema JSON) — candidatos personalizados           #
# --------------------------------------------------------------------------- #
# Descrição das alavancas, compartilhada pelos prompts de geração e de refino.
_ALAVANCAS_PROMPT = (
    "ALAVANCAS das diretrizes: prioridades (1 a 5, "
    "chave = id da tarefa); ajustes_por_tarefa (buffer_dias ≥ 0, "
    "max_min_por_dia ≥ 1); max_min_por_dia_total (teto diário somando tudo); "
    "janela_por_dia (chave '0'..'6' = dia da semana, 0=segunda, OU data "
    "'YYYY-MM-DD'; valor ['HH:MM','HH:MM'] entre 05:00 e 23:59 — pode encolher "
    "OU estender a janela); usar_fds (true libera fim de semana); "
    "dias_bloqueados (datas 'YYYY-MM-DD' sem nenhuma sessão). Datas SEMPRE "
    "entre 'agora' e 'horizonte_fim' dos FATOS. "
)

SYSTEM_PROMPT_CENARIOS = (
    "Você propõe CENÁRIOS alternativos de rotina para o usuário escolher. "
    "Receberá FATOS já calculados do plano atual. NUNCA invente números, "
    "horários ou datas — derive tudo dos FATOS. Proponha de 3 a 6 cenários "
    "DIFERENTES entre si, cada um com uma intenção clara de trade-off (ex.: "
    "ganhar o sábado estendendo a quinta; suavizar picos; terminar com folga). "
    "Cada cenário tem: nome (curto, em português), intencao (1 frase com o "
    "trade-off) e diretrizes. " + _ALAVANCAS_PROMPT + "Use 'carga_por_dia' para achar "
    "picos e dias recuperáveis. Os FATOS trazem também o comportamento REAL do "
    "usuário: 'fatores_classe' (razão real/estimado — acima de 1 significa que "
    "a classe costuma demorar mais que o previsto), 'flexibilidade_classe' "
    "(0..1, taxa de remarcação — classes flexíveis são as melhores candidatas "
    "a mover; as rígidas ficam intocadas) e 'pesos_preferencia' (o que ele "
    "valoriza nas métricas — proponha cenários alinhados a isso). Nos textos, "
    "refira-se às tarefas pelo título, nunca pelo id, e não cite nomes "
    "técnicos de campos. Responda no schema."
)

_SCHEMA_DIRETRIZES = {
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
        "janela_por_dia": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 2,
            },
        },
        "usar_fds": {"type": "boolean"},
        "dias_bloqueados": {"type": "array", "items": {"type": "string"}},
    },
}

SCHEMA_CENARIOS = {
    "type": "object",
    "properties": {
        "cenarios": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "intencao": {"type": "string"},
                    "diretrizes": _SCHEMA_DIRETRIZES,
                },
                "required": ["nome", "intencao", "diretrizes"],
            },
        },
    },
    "required": ["cenarios"],
}


def gerar_cenarios_ia(contexto):
    """UMA chamada ao Ollama → lista bruta de candidatos {nome, intencao, diretrizes}.

    Mesmo padrão de gerar_melhoria: qualquer falha vira OllamaIndisponivel e o
    caller degrada para só os arquétipos. temperature 0.
    """
    try:
        cli = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT
        )
        resp = cli.chat(
            model=settings.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_CENARIOS},
                {"role": "user", "content": json.dumps(contexto, ensure_ascii=False)},
            ],
            format=SCHEMA_CENARIOS,
            options={"temperature": 0},
        )
        bruto = json.loads(resp["message"]["content"])
        cenarios = bruto.get("cenarios")
        if not isinstance(cenarios, list):
            raise ValueError("resposta sem lista de cenários")
        return cenarios
    except Exception as e:  # rede, timeout, JSON inválido, shape errado
        raise OllamaIndisponivel(str(e))


# --------------------------------------------------------------------------- #
# Refino conversacional (C5) — "gostei do B, mas sem academia essa semana"     #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT_REFINO = (
    "Você AJUSTA cenários de rotina conforme o pedido do usuário, numa "
    "conversa. Receberá FATOS já calculados, os CENÁRIOS atuais (cada um com "
    "diretrizes e métricas) e o pedido em linguagem natural. NUNCA invente "
    "números, horários ou datas — derive tudo dos FATOS. Produza UM cenário "
    "novo: parta das diretrizes do cenário em 'cenario_em_foco' (se o pedido "
    "citar outro cenário pelo nome, parta dele) e mude SOMENTE o que o pedido "
    "exigir, preservando o resto. "
    + _ALAVANCAS_PROMPT
    + "REGRA OBRIGATÓRIA — excluir_tarefas: sempre que o pedido disser que o "
    "usuário NÃO vai fazer uma tarefa ('sem academia', 'tira o relatório', "
    "'pulo o inglês'), você DEVE colocar o id dessa tarefa (ache em 'tarefas' "
    "dos FATOS pelo título) na lista excluir_tarefas das diretrizes — ela "
    "REMOVE a tarefa do plano. NUNCA simule a remoção com prioridades, tetos "
    "ou janelas: esses ajustes NÃO removem nada. EXEMPLO: pedido 'não vou "
    "fazer academia essa semana' com a tarefa 'Academia' de id 'abc-1' nos "
    "FATOS ⇒ diretrizes = as do cenário de origem MAIS "
    '{"excluir_tarefas": ["abc-1"]}, sem nenhum outro ajuste novo. '
    "Além das diretrizes, escreva: resposta (1 a 3 frases confirmando o que "
    "mudou e o trade-off; se o pedido for impossível ou não fizer sentido "
    "com os FATOS, diga o porquê), nome (curto, derivado do cenário de "
    "origem, ex.: 'Ritmo leve — sem academia') e intencao (1 frase). Nos "
    "textos, refira-se às tarefas pelo título, nunca pelo id, e não cite "
    "nomes técnicos de campos. Responda no schema."
)

_SCHEMA_DIRETRIZES_REFINO = {
    "type": "object",
    "properties": {
        **_SCHEMA_DIRETRIZES["properties"],
        "excluir_tarefas": {"type": "array", "items": {"type": "string"}},
    },
}

SCHEMA_REFINO = {
    "type": "object",
    "properties": {
        "resposta": {"type": "string"},
        "nome": {"type": "string"},
        "intencao": {"type": "string"},
        "diretrizes": _SCHEMA_DIRETRIZES_REFINO,
    },
    "required": ["resposta", "nome", "intencao", "diretrizes"],
}


def refinar_cenario_ia(contexto, historico, mensagem):
    """UMA chamada ao Ollama → {resposta, nome, intencao, diretrizes} bruto.

    `contexto` já traz os FATOS + lote atual + cenário em foco; `historico` é a
    conversa anterior deste lote (lista {role, content}), reenviada para o
    modelo manter o fio ("agora também sem o sábado"). Mesmo padrão de
    degradação dos irmãos: qualquer falha vira OllamaIndisponivel.
    """
    try:
        cli = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT
        )
        resp = cli.chat(
            model=settings.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_REFINO},
                {"role": "user", "content": json.dumps(contexto, ensure_ascii=False)},
                *historico,
                {"role": "user", "content": mensagem},
            ],
            format=SCHEMA_REFINO,
            options={"temperature": 0},
        )
        bruto = json.loads(resp["message"]["content"])
        if not isinstance(bruto, dict):
            raise ValueError("resposta de refino não é um objeto")
        return bruto
    except Exception as e:  # rede, timeout, JSON inválido, shape errado
        raise OllamaIndisponivel(str(e))


# --------------------------------------------------------------------------- #
# Métricas por CÓDIGO (comparáveis entre cenários)                             #
# --------------------------------------------------------------------------- #
def metricas_do_plano(res):
    """Mede um ResultadoPlano nas métricas de comparação (§2.3).

    `min_fora_janela` é medido contra a janela do USUÁRIO (prefs globais), não
    contra a do cenário — é justamente o custo que o cenário explicita.
    """
    tz = timezone.get_current_timezone()
    carga_por_dia = {}
    ultima_sessao = {}
    fora_janela = 0
    win_ini = res.prefs.janela_inicio_min
    win_fim = res.prefs.janela_fim_min
    for s in res.sessoes:
        ini_local = timezone.localtime(s.inicio, tz)
        dia = ini_local.date()
        carga_por_dia[dia] = carga_por_dia.get(dia, 0) + s.dur_min
        atual = ultima_sessao.get(s.tarefa_id)
        if atual is None or s.fim > atual:
            ultima_sessao[s.tarefa_id] = s.fim
        if res.prefs.evitar_fds and dia.weekday() >= 5:
            fora_janela += s.dur_min
            continue
        meia_noite = ini_local.replace(hour=0, minute=0, second=0, microsecond=0)
        ini_min = int((ini_local - meia_noite).total_seconds() // 60)
        fim_min = ini_min + s.dur_min
        dentro = max(0, min(fim_min, win_fim) - max(ini_min, win_ini))
        fora_janela += s.dur_min - dentro

    dias_livres = 0
    fds_livres = 0
    dia = timezone.localtime(res.agora, tz).date()
    ultimo = timezone.localtime(res.horizonte_fim, tz).date()
    while dia <= ultimo:
        if dia not in carga_por_dia:
            dias_livres += 1
            if dia.weekday() >= 5:
                fds_livres += 1
        dia += timedelta(days=1)

    deadlines = {te.id: te.deadline for te in res.tarefas}
    folgas_h = [
        (deadlines[tid] - fim).total_seconds() / 3600
        for tid, fim in ultima_sessao.items()
        if tid in deadlines
    ]

    return {
        "pico_min_dia": max(carga_por_dia.values()) if carga_por_dia else 0,
        "dias_livres": dias_livres,
        "fds_livres": fds_livres,
        "folga_media_h": round(sum(folgas_h) / len(folgas_h), 1) if folgas_h else 0.0,
        "min_fora_janela": fora_janela,
        "fragmentacao": round(len(res.sessoes) / max(len(res.tarefas), 1), 2),
        "nao_alocado_min": sum(n.minutos_restantes for n in res.nao_alocado),
    }


def normalizar(m, m_base):
    """Métricas relativas ao plano base do lote, com "maior = melhor" em todas.

    Delta relativo ao base (unidades diferentes → pesos comparáveis entre si e
    entre semanas); custo tem o sinal invertido. O base fica com tudo 0.
    """
    norm = {}
    for met in METRICAS:
        escala = max(abs(m_base[met]), 1.0)
        delta = (m[met] - m_base[met]) / escala
        norm[met] = round(delta if met in METRICAS_BENEFICIO else -delta, 4)
    return norm


# --------------------------------------------------------------------------- #
# Dominância, pontuação e narrativa                                            #
# --------------------------------------------------------------------------- #
def _chave_plano(res):
    return tuple(
        (s.tarefa_id, s.inicio.isoformat(), s.fim.isoformat()) for s in res.sessoes
    )


def filtrar_dominados(cenarios):
    """Poda o objetivamente ruim; sobrevivem só trade-offs legítimos.

    - plano idêntico a um anterior (base > arquétipos > IA) → descartado;
    - `nao_alocado_min` pior que o do base → eliminado direto (métrica dominante);
    - pior-ou-igual em TODAS as métricas que outro cenário → dominado, descartado
      (empate total mantém o de posição anterior). O base nunca é descartado.
    """
    vivos = []
    vistos = set()
    for c in cenarios:
        chave = _chave_plano(c["res"])
        if c["id"] != "base" and chave in vistos:
            continue
        vistos.add(chave)
        vivos.append(c)

    base = next(c for c in vivos if c["id"] == "base")
    vivos = [
        c
        for c in vivos
        if c is base
        or c["metricas"]["nao_alocado_min"] <= base["metricas"]["nao_alocado_min"]
    ]

    resultado = []
    for i, c in enumerate(vivos):
        if c is base:
            resultado.append(c)
            continue
        dominado = False
        n = c["metricas_vs_base"]
        for j, outro in enumerate(vivos):
            if i == j:
                continue
            o = outro["metricas_vs_base"]
            melhor_igual = all(o[m] >= n[m] for m in METRICAS)
            estrito = any(o[m] > n[m] for m in METRICAS)
            if melhor_igual and (estrito or j < i):
                dominado = True
                break
        if not dominado:
            resultado.append(c)
    return resultado


def pontuar(cenarios, pesos):
    """score = Σ peso_m × métrica_normalizada_m; seleciona no máx. MAX_CENARIOS.

    O maior score vira o "Sugerido" (pré-selecionado, nunca filtra os demais).
    Diversidade garantida: o retorno SEMPRE inclui o base e ≥1 "contrariante"
    (o melhor na métrica de menor peso atual) — escolhê-lo é o sinal de que o
    gosto mudou, e o EWMA corrige.
    """
    for c in cenarios:
        c["score"] = round(
            sum(pesos.get(m, 1.0) * c["metricas_vs_base"][m] for m in METRICAS), 4
        )
    ordenados = sorted(cenarios, key=lambda c: c["score"], reverse=True)

    base = next(c for c in ordenados if c["id"] == "base")
    metrica_fraca = min(METRICAS, key=lambda m: pesos.get(m, 1.0))
    outros = [c for c in ordenados if c is not base]
    contrariante = max(
        outros, key=lambda c: c["metricas_vs_base"][metrica_fraca], default=None
    )

    selecao = []
    prioridade = [ordenados[0], base, contrariante] + ordenados
    for c in prioridade:
        if c is not None and c not in selecao and len(selecao) < MAX_CENARIOS:
            selecao.append(c)

    selecao.sort(key=lambda c: c["score"], reverse=True)
    for c in selecao:
        c["sugerido"] = c is selecao[0]
    return selecao


def _fmt_min(minutos):
    horas, resto = divmod(int(minutos), 60)
    if horas and resto:
        return f"{horas}h{resto:02d}"
    if horas:
        return f"{horas}h"
    return f"{resto}min"


def narrar(cenario, metricas_base):
    """Trade-offs por template de código sobre o diff de métricas vs o base.

    Frases factuais e curtas ("sábado livre", "pico cai de 6h para 4h").
    Polimento por IA é adiável — o template já é grounded.
    """
    m, b = cenario["metricas"], metricas_base
    frases = []
    if m["fds_livres"] > b["fds_livres"]:
        frases.append(
            f"+{m['fds_livres'] - b['fds_livres']} dia(s) de fim de semana livre(s)"
        )
    elif m["fds_livres"] < b["fds_livres"]:
        frases.append(
            f"usa {b['fds_livres'] - m['fds_livres']} dia(s) do fim de semana"
        )
    if m["pico_min_dia"] < b["pico_min_dia"]:
        frases.append(
            f"pico diário cai de {_fmt_min(b['pico_min_dia'])} para {_fmt_min(m['pico_min_dia'])}"
        )
    elif m["pico_min_dia"] > b["pico_min_dia"]:
        frases.append(
            f"pico diário sobe de {_fmt_min(b['pico_min_dia'])} para {_fmt_min(m['pico_min_dia'])}"
        )
    if m["dias_livres"] > b["dias_livres"]:
        frases.append(
            f"+{m['dias_livres'] - b['dias_livres']} dia(s) totalmente livre(s)"
        )
    elif m["dias_livres"] < b["dias_livres"]:
        frases.append(f"{b['dias_livres'] - m['dias_livres']} dia(s) livre(s) a menos")
    if m["folga_media_h"] > b["folga_media_h"]:
        frases.append(
            f"termina com +{round(m['folga_media_h'] - b['folga_media_h'], 1)}h de folga média antes dos prazos"
        )
    elif m["folga_media_h"] < b["folga_media_h"]:
        frases.append(
            f"folga média antes dos prazos cai {round(b['folga_media_h'] - m['folga_media_h'], 1)}h"
        )
    if m["min_fora_janela"] > b["min_fora_janela"]:
        frases.append(
            f"usa {_fmt_min(m['min_fora_janela'] - b['min_fora_janela'])} fora da janela preferida"
        )
    if m["nao_alocado_min"] < b["nao_alocado_min"]:
        frases.append(
            f"aloca {_fmt_min(b['nao_alocado_min'] - m['nao_alocado_min'])} a mais dentro do prazo"
        )
    return frases


# --------------------------------------------------------------------------- #
# Identificadores                                                              #
# --------------------------------------------------------------------------- #
def slug_cenario(nome, usados):
    """Id estável e único a partir do nome ("Sábado livre" → "sabado-livre")."""
    ascii_ = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_.lower().strip()).strip("-") or "cenario"
    slug = base[:50]
    n = 2
    while slug in usados:
        slug = f"{base[:46]}-{n}"
        n += 1
    usados.add(slug)
    return slug
