"""Planejador de produção multitarefa (guloso EDF + anti-conflito + relaxamento).

Calcula um único plano de sessões para várias tarefas de uma vez, sem conflitar
com eventos existentes (simples E recorrentes expandidos) nem entre si. É uma
função pura sobre os dados de entrada — NÃO persiste nada (isso é do `aplicar`).

Decisões (ver docs/tasks/planejamento-producao-multitarefa.md):
- ocorrências recorrentes continuam virtuais; aqui expandimos sob demanda no
  horizonte, reusando services.recurrence.expandir (igual EventoViewSet.list).
- as preferências são SUAVES: a janela de horário é só uma preferência — se o
  plano não couber dentro dela antes da deadline, o relaxamento libera o dia
  inteiro (24h) e, por fim, sessões menores que sessao_min.
"""

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from ..models import Evento, Tarefa
from . import holidays
from .recurrence import expandir

# Horizonte máximo do planejamento (~92 dias). Vive aqui (não na view) porque a
# orquestração — `montar_plano` — também é usada pela task de IA.
JANELA_MAX = timedelta(days=92)

# Horizontes que o cliente pode escolher (teto do escopo do plano, em dias).
# AUTOMATICO (None) ⇒ vai até o deadline mais distante, com teto em JANELA_MAX.
# Os demais limitam a janela: tarefas que não couberem caem em `nao_alocado`.
HORIZONTES = {
    "AUTOMATICO": None,
    "SEMANA": 7,
    "DUAS_SEMANAS": 14,
    "MES": 30,
}

# Defaults das preferências (handoff §5). Horários como "HH:MM"; minutos como int.
DEFAULTS = {
    "janela_inicio": "08:00",
    "janela_fim": "22:00",
    "evitar_fds": True,
    "max_min_por_dia_por_tarefa": 120,
    "max_min_por_dia_total": None,
    "sessao_min": 30,
    "sessao_max": 120,
    "granularidade_min": 15,
}

# Ordem do relaxamento por tarefa (§4, passo 4). Cada nível afrouxa mais uma
# preferência e re-tenta só o que falta. O nível 5 só libera `dias_bloqueados`
# (a preferência mais "dura"); sem diretrizes novas é idêntico ao 4.
NIVEIS = (0, 1, 2, 3, 4, 5)
MIN_POR_DIA = 24 * 60


@dataclass
class Preferencias:
    """Preferências efetivas já normalizadas (janelas em minutos do dia).

    Os 3 últimos campos são alavancas de CENÁRIO (Marco C1a), setadas só via
    diretrizes em `montar_plano`; os defaults neutros preservam o comportamento
    atual. `janela_por_dia`: chave "0".."6" (dia da semana) ou "YYYY-MM-DD"
    (data; vence o dia da semana) → (inicio_min, fim_min).
    """

    janela_inicio_min: int
    janela_fim_min: int
    evitar_fds: bool
    max_min_por_dia_por_tarefa: int | None
    max_min_por_dia_total: int | None
    sessao_min: int
    sessao_max: int
    granularidade: int
    janela_por_dia: dict | None = None
    usar_fds: bool | None = None  # True libera o fds já no nível 0
    dias_bloqueados: frozenset = frozenset()  # datas (date) sem NENHUMA sessão


@dataclass
class _PrefsNivel:
    """Snapshot das preferências para um nível de relaxamento."""

    evitar_fds: bool
    janela_inicio_min: int
    janela_fim_min: int
    max_dia_tarefa: int | None
    max_dia_total: int | None
    sessao_min: int
    janela_por_dia: dict | None = None
    dias_bloqueados: frozenset = field(default_factory=frozenset)


@dataclass
class TarefaEntrada:
    """Tarefa elegível, já validada pela view (tem deadline/esforço/classe).

    Os 3 últimos campos são "knobs" opcionais que a IA pode setar via diretrizes
    (Fase A). Os defaults preservam exatamente o comportamento do solver puro.
    """

    id: str
    titulo: str
    classe_id: str
    esforco: int  # minutos
    deadline: datetime
    prioridade: int | None = None  # 1..5 (None ⇒ neutro = 3); desempate do EDF
    buffer_dias: int = 0  # terminar N dias antes da deadline
    max_min_por_dia: int | None = None  # teto diário específico desta tarefa


@dataclass
class Sessao:
    tarefa_id: str
    tarefa_titulo: str
    classe_id: str
    inicio: datetime
    fim: datetime
    dur_min: int


@dataclass
class NaoAlocado:
    tarefa_id: str
    tarefa_titulo: str
    minutos_restantes: int
    motivo: str


# --------------------------------------------------------------------------- #
# Preferências                                                                 #
# --------------------------------------------------------------------------- #
def _hhmm_para_min(valor):
    horas, minutos = valor.split(":")
    return int(horas) * 60 + int(minutos)


def montar_preferencias(entrada):
    """Mescla a entrada com os defaults. Retorna (Preferencias, dict_ecoado).

    `entrada` traz só as chaves que o cliente enviou (null explícito é
    respeitado, ex.: zerar um teto de minutos/dia).
    """
    usadas = dict(DEFAULTS)
    usadas.update(entrada)
    prefs = Preferencias(
        janela_inicio_min=_hhmm_para_min(usadas["janela_inicio"]),
        janela_fim_min=_hhmm_para_min(usadas["janela_fim"]),
        evitar_fds=usadas["evitar_fds"],
        max_min_por_dia_por_tarefa=usadas["max_min_por_dia_por_tarefa"],
        max_min_por_dia_total=usadas["max_min_por_dia_total"],
        sessao_min=usadas["sessao_min"],
        sessao_max=usadas["sessao_max"],
        granularidade=usadas["granularidade_min"],
    )
    return prefs, usadas


def _prefs_do_nivel(prefs, nivel):
    """Aplica o relaxamento acumulado até `nivel` (§4, passo 4)."""
    evitar_fds = prefs.evitar_fds
    if prefs.usar_fds:  # escolha explícita do cenário: fds liberado desde o 0
        evitar_fds = False
    janela_inicio = prefs.janela_inicio_min
    janela_fim = prefs.janela_fim_min
    max_tarefa = prefs.max_min_por_dia_por_tarefa
    max_total = prefs.max_min_por_dia_total
    sessao_min = prefs.sessao_min
    if nivel >= 1:  # 1) permitir fins de semana
        evitar_fds = False
    if nivel >= 2:  # 2) remover tetos de minutos/dia
        max_tarefa = None
        max_total = None
    if nivel >= 3:  # 3) estender a janela para o dia inteiro (24h)
        janela_inicio = 0
        janela_fim = MIN_POR_DIA
    if nivel >= 4:  # 4) permitir sessões menores que sessao_min
        sessao_min = prefs.granularidade
    return _PrefsNivel(
        evitar_fds,
        janela_inicio,
        janela_fim,
        max_tarefa,
        max_total,
        sessao_min,
        # Overrides por dia valem enquanto a janela do usuário vale; no 24h
        # (nível ≥ 3) o relaxamento final continua soberano.
        janela_por_dia=prefs.janela_por_dia if nivel < 3 else None,
        # 5) liberar dias bloqueados — último recurso antes de `nao_alocado`.
        dias_bloqueados=prefs.dias_bloqueados if nivel < 5 else frozenset(),
    )


# --------------------------------------------------------------------------- #
# Eventos ocupados                                                             #
# --------------------------------------------------------------------------- #
def intervalos_ocupados(agora, horizonte_fim, excluir_evento_ids=None):
    """Intervalos [inicio, fim] bloqueados no horizonte, já mesclados.

    Cobre eventos simples (query direta) e recorrentes (expandidos sob demanda,
    igual EventoViewSet.list). Ocorrências PULADAS são omitidas por `expandir`.
    `excluir_evento_ids` tira eventos simples do bloqueio — o replanejar (C2)
    exclui as sessões que serão substituídas, senão elas se auto-bloqueiam.
    """
    intervalos = []

    simples = Evento.objects.filter(
        regra_recorrencia__isnull=True, inicio__lt=horizonte_fim, fim__gt=agora
    ).only("inicio", "fim")
    if excluir_evento_ids:
        simples = simples.exclude(id__in=excluir_evento_ids)
    for ev in simples:
        intervalos.append((ev.inicio, ev.fim))

    feriados = set()
    for ano in range(agora.year, horizonte_fim.year + 1):
        feriados |= holidays.feriados_do_ano(ano)

    recorrentes = (
        Evento.objects.filter(regra_recorrencia__isnull=False)
        .select_related("regra_recorrencia")
        .prefetch_related("ocorrencias")
    )
    for ev in recorrentes:
        for view in expandir(ev, agora, horizonte_fim, feriados):
            intervalos.append((view.inicio, view.fim))

    return _mesclar(intervalos)


def _mesclar(intervalos):
    """Ordena por início e funde intervalos sobrepostos/adjacentes."""
    if not intervalos:
        return []
    ordenados = sorted(intervalos, key=lambda iv: iv[0])
    mesclado = [ordenados[0]]
    for inicio, fim in ordenados[1:]:
        ult_inicio, ult_fim = mesclado[-1]
        if inicio <= ult_fim:
            if fim > ult_fim:
                mesclado[-1] = (ult_inicio, fim)
        else:
            mesclado.append((inicio, fim))
    return mesclado


# --------------------------------------------------------------------------- #
# Slots livres                                                                 #
# --------------------------------------------------------------------------- #
def _midnight_aware(dia, tz):
    return timezone.make_aware(datetime.combine(dia, time.min), tz)


def _snap_acima(dt, granularidade, tz):
    """Arredonda `dt` para cima no grid da granularidade (relativo à meia-noite local)."""
    local = timezone.localtime(dt, tz)
    base = local.replace(hour=0, minute=0, second=0, microsecond=0)
    delta_min = (local - base).total_seconds() / 60
    snapped = math.ceil(delta_min / granularidade) * granularidade
    return base + timedelta(minutes=snapped)


def _janela_do_dia(dia, pn):
    """(ini_min, fim_min) do dia ou None (dia fora do plano).

    Precedência: bloqueio > fim de semana (se `evitar_fds`) > override por data
    > override por dia-da-semana > janela global do nível. Os overrides e
    bloqueios já chegam filtrados por nível via `_prefs_do_nivel` (níveis ≥ 3
    ignoram overrides; o 5 libera bloqueios). Um override por data NÃO libera o
    fim de semana — para isso existe `usar_fds`.
    """
    if dia in pn.dias_bloqueados:
        return None
    if pn.evitar_fds and dia.weekday() >= 5:
        return None
    if pn.janela_por_dia:
        override = pn.janela_por_dia.get(dia.isoformat()) or pn.janela_por_dia.get(
            str(dia.weekday())
        )
        if override:
            return override
    return (pn.janela_inicio_min, pn.janela_fim_min)


def slots_livres(inicio_busca, fim_busca, pn, granularidade, ocupado):
    """Intervalos livres dentro de [inicio_busca, fim_busca], dia a dia.

    Para cada dia resolve a janela via `_janela_do_dia` (horário local; pula
    dias bloqueados e fim de semana se `pn.evitar_fds`), subtrai `ocupado` e
    snapa os inícios na granularidade. Devolve em ordem cronológica.
    """
    tz = timezone.get_current_timezone()
    slots = []
    dia = timezone.localtime(inicio_busca, tz).date()
    ultimo = timezone.localtime(fim_busca, tz).date()
    while dia <= ultimo:
        janela = _janela_do_dia(dia, pn)
        if janela is not None:
            ini_min, fim_min = janela
            meia_noite = _midnight_aware(dia, tz)
            janela_ini = meia_noite + timedelta(minutes=ini_min)
            janela_fim = meia_noite + timedelta(minutes=fim_min)
            ini = max(janela_ini, inicio_busca)
            fim = min(janela_fim, fim_busca)
            if fim > ini:
                slots.extend(_subtrair_ocupado(ini, fim, ocupado, granularidade, tz))
        dia += timedelta(days=1)
    return slots


def _subtrair_ocupado(ini, fim, ocupado, granularidade, tz):
    """[ini, fim] menos os intervalos `ocupado` (ordenados), com inícios snapados."""
    livres = []
    cursor = ini
    for oc_ini, oc_fim in ocupado:
        if oc_fim <= cursor:
            continue
        if oc_ini >= fim:
            break
        if oc_ini > cursor:
            livres.append((cursor, min(oc_ini, fim)))
        cursor = max(cursor, oc_fim)
        if cursor >= fim:
            break
    if cursor < fim:
        livres.append((cursor, fim))

    snapped = []
    for s_ini, s_fim in livres:
        s_ini = _snap_acima(s_ini, granularidade, tz)
        if s_fim > s_ini:
            snapped.append((s_ini, s_fim))
    return snapped


# --------------------------------------------------------------------------- #
# Núcleo guloso                                                                #
# --------------------------------------------------------------------------- #
def _deadline_efetiva(tarefa, agora):
    """Deadline antecipada pelo buffer; se o buffer a jogar pro passado, ignora."""
    if not tarefa.buffer_dias:
        return tarefa.deadline
    efetiva = tarefa.deadline - timedelta(days=tarefa.buffer_dias)
    return efetiva if efetiva > agora else tarefa.deadline


def calcular_plano(tarefas, ocupado, prefs, agora, horizonte_fim):
    """Aloca sessões para todas as tarefas. Retorna (sessoes, nao_alocado)."""
    sessoes = []
    nao_alocado = []
    ocupado = list(ocupado)  # mutável: cada sessão colocada vira "ocupado"
    min_tarefa_dia = {}  # (tarefa_id, date) -> minutos já alocados
    min_total_dia = {}  # date -> minutos já alocados (todas as tarefas)
    tz = timezone.get_current_timezone()

    # EDF: deadline (efetiva) asc; prioridade desempata (maior primeiro), depois
    # menor folga e maior esforço. Prazo continua dominando (factibilidade).
    tarefas_ord = sorted(
        tarefas,
        key=lambda t: (
            _deadline_efetiva(t, agora),
            -(t.prioridade or 3),
            (_deadline_efetiva(t, agora) - agora) - timedelta(minutes=t.esforco),
            -t.esforco,
        ),
    )

    for tarefa in tarefas_ord:
        deadline_efetiva = _deadline_efetiva(tarefa, agora)
        if deadline_efetiva <= agora:
            nao_alocado.append(
                NaoAlocado(
                    tarefa.id, tarefa.titulo, tarefa.esforco, "deadline no passado"
                )
            )
            continue

        restante = tarefa.esforco
        fim_busca = min(deadline_efetiva, horizonte_fim)
        for nivel in NIVEIS:
            if restante <= 0:
                break
            pn = _prefs_do_nivel(prefs, nivel)
            slots = slots_livres(agora, fim_busca, pn, prefs.granularidade, ocupado)
            restante = _alocar(
                tarefa,
                restante,
                slots,
                pn,
                prefs,
                sessoes,
                ocupado,
                min_tarefa_dia,
                min_total_dia,
                tz,
            )

        if restante > 0:
            nao_alocado.append(
                NaoAlocado(
                    tarefa.id,
                    tarefa.titulo,
                    restante,
                    "sem espaço livre antes da deadline",
                )
            )

    sessoes.sort(key=lambda s: s.inicio)
    return sessoes, nao_alocado


def _alocar(
    tarefa,
    restante,
    slots,
    pn,
    prefs,
    sessoes,
    ocupado,
    min_tarefa_dia,
    min_total_dia,
    tz,
):
    """Encaixa sessões da tarefa nos slots (cronológico). Devolve o que sobrou."""
    for s_ini, s_fim in slots:
        if restante <= 0:
            break
        dia = timezone.localtime(s_ini, tz).date()
        tamanho = int((s_fim - s_ini).total_seconds() // 60)
        if tamanho <= 0:
            continue

        limites = [restante, prefs.sessao_max, tamanho]
        # Teto diário da tarefa: o global (já relaxado por nível) e o override
        # específico desta tarefa se combinam pelo menor. Override é suave: quando
        # o relaxamento zera o teto global (nível ≥ 2), o override some junto.
        cap_tarefa = pn.max_dia_tarefa
        if cap_tarefa is not None and tarefa.max_min_por_dia is not None:
            cap_tarefa = min(cap_tarefa, tarefa.max_min_por_dia)
        if cap_tarefa is not None:
            limites.append(cap_tarefa - min_tarefa_dia.get((tarefa.id, dia), 0))
        if pn.max_dia_total is not None:
            limites.append(pn.max_dia_total - min_total_dia.get(dia, 0))
        dur = min(limites)
        if dur <= 0:
            continue
        # Respeita sessao_min, salvo quando o que falta já é menor que ele
        # (resto final) — senão a tarefa nunca fecharia.
        if dur < min(pn.sessao_min, restante):
            continue

        fim = s_ini + timedelta(minutes=dur)
        sessoes.append(
            Sessao(tarefa.id, tarefa.titulo, tarefa.classe_id, s_ini, fim, dur)
        )
        ocupado.append((s_ini, fim))
        ocupado[:] = _mesclar(ocupado)
        min_tarefa_dia[(tarefa.id, dia)] = min_tarefa_dia.get((tarefa.id, dia), 0) + dur
        min_total_dia[dia] = min_total_dia.get(dia, 0) + dur
        restante -= dur

    return restante


# --------------------------------------------------------------------------- #
# Orquestração (reusada por /calcular e pela task de IA)                       #
# --------------------------------------------------------------------------- #
@dataclass
class ResultadoPlano:
    """Plano calculado + tudo o que a construção do contexto/alertas precisa."""

    sessoes: list
    nao_alocado: list
    prefs: Preferencias
    prefs_usadas: dict
    tarefas: list  # list[TarefaEntrada] já com as diretrizes aplicadas
    ocupado: list
    agora: datetime
    horizonte_fim: datetime


def validar_tarefas(tarefa_ids):
    """Separa elegíveis de inválidas (mesma regra do /calcular de hoje).

    Inválida = inexistente / já PROMOVIDA / sem deadline|esforço|classe. Aceita
    ids como UUID ou str (a task recebe strings). Retorna
    `(validas: list[Tarefa], invalidas: list[{tarefa_id, motivo}])`.
    """
    ids = list(dict.fromkeys(str(t) for t in tarefa_ids))  # dedup preservando ordem
    por_id = {
        str(t.id): t for t in Tarefa.objects.select_related("classe").filter(id__in=ids)
    }
    validas = []
    invalidas = []
    for tid in ids:
        tarefa = por_id.get(tid)
        if tarefa is None:
            invalidas.append({"tarefa_id": tid, "motivo": "tarefa inexistente"})
            continue
        if tarefa.status == Tarefa.Status.PROMOVIDA:
            invalidas.append({"tarefa_id": tid, "motivo": "tarefa já promovida"})
            continue
        faltando = []
        if tarefa.deadline is None:
            faltando.append("deadline")
        if not tarefa.esforco_estimado:
            faltando.append("esforco_estimado")
        if tarefa.classe_id is None:
            faltando.append("classe")
        if faltando:
            invalidas.append(
                {"tarefa_id": tid, "motivo": f"faltando: {', '.join(faltando)}"}
            )
            continue
        validas.append(tarefa)
    return validas, invalidas


def montar_plano(
    tarefas_validas,
    agora,
    preferencias_entrada,
    diretrizes=None,
    horizonte_dias=None,
    excluir_evento_ids=None,
):
    """Monta as TarefaEntrada (aplicando `diretrizes`), define o horizonte e roda
    o solver. `diretrizes` é o dict já validado (ver planejamento_ia); ausente ⇒
    comportamento idêntico ao plano base. `horizonte_dias` (None ⇒ AUTOMATICO)
    limita a janela do plano; o que não couber cai em `nao_alocado`.
    `excluir_evento_ids` repassa ao `intervalos_ocupados` (replanejar, C2).
    Retorna ResultadoPlano.
    """
    prefs, prefs_usadas = montar_preferencias(preferencias_entrada or {})
    diretrizes = diretrizes or {}
    prioridades = diretrizes.get("prioridades", {})
    ajustes = diretrizes.get("ajustes_por_tarefa", {})

    # Teto diário total da IA (suavizar picos): só APERTA — nunca afrouxa o que o
    # usuário pediu. O relaxamento o remove sozinho (nível ≥ 2) se algo não couber.
    teto_total_ia = diretrizes.get("max_min_por_dia_total")
    if teto_total_ia is not None:
        atual = prefs.max_min_por_dia_total
        novo = teto_total_ia if atual is None else min(atual, teto_total_ia)
        prefs = replace(prefs, max_min_por_dia_total=novo)
        prefs_usadas = {**prefs_usadas, "max_min_por_dia_total": novo}

    # Alavancas de CENÁRIO (C1a). Diferente do teto acima, podem encolher OU
    # estender a janela do usuário: o cenário é proposta explícita que ele verá
    # e aceitará, não um ajuste silencioso. Chegam já validadas (guarda-corpo).
    janela_por_dia = diretrizes.get("janela_por_dia")
    if janela_por_dia:
        prefs = replace(
            prefs,
            janela_por_dia={
                chave: (_hhmm_para_min(ini), _hhmm_para_min(fim))
                for chave, (ini, fim) in janela_por_dia.items()
            },
        )
        prefs_usadas = {**prefs_usadas, "janela_por_dia": janela_por_dia}
    usar_fds = diretrizes.get("usar_fds")
    if usar_fds is not None:
        prefs = replace(prefs, usar_fds=usar_fds)
        prefs_usadas = {**prefs_usadas, "usar_fds": usar_fds}
    dias_bloqueados = diretrizes.get("dias_bloqueados")
    if dias_bloqueados:
        prefs = replace(
            prefs,
            dias_bloqueados=frozenset(date.fromisoformat(d) for d in dias_bloqueados),
        )
        prefs_usadas = {**prefs_usadas, "dias_bloqueados": sorted(dias_bloqueados)}

    tarefas = []
    for t in tarefas_validas:
        tid = str(t.id)
        aj = ajustes.get(tid, {})
        tarefas.append(
            TarefaEntrada(
                id=tid,
                titulo=t.titulo,
                classe_id=str(t.classe_id),
                esforco=t.esforco_estimado,
                deadline=t.deadline,
                prioridade=prioridades.get(tid),
                buffer_dias=aj.get("buffer_dias", 0) or 0,
                max_min_por_dia=aj.get("max_min_por_dia"),
            )
        )

    teto = agora + (timedelta(days=horizonte_dias) if horizonte_dias else JANELA_MAX)
    horizonte_fim = min(max(_deadline_efetiva(te, agora) for te in tarefas), teto)
    ocupado = intervalos_ocupados(agora, horizonte_fim, excluir_evento_ids)
    sessoes, nao_alocado = calcular_plano(tarefas, ocupado, prefs, agora, horizonte_fim)
    return ResultadoPlano(
        sessoes=sessoes,
        nao_alocado=nao_alocado,
        prefs=prefs,
        prefs_usadas=prefs_usadas,
        tarefas=tarefas,
        ocupado=ocupado,
        agora=agora,
        horizonte_fim=horizonte_fim,
    )


def serializar_plano(res):
    """Shape de saída do plano — idêntico ao do /calcular de hoje.

    Compartilhado entre a view /calcular e a task de IA (por isso vive aqui, e
    não em views: evita import circular).
    """
    return {
        "sessoes": [
            {
                "tarefa_id": s.tarefa_id,
                "tarefa_titulo": s.tarefa_titulo,
                "classe_id": s.classe_id,
                "inicio": s.inicio.isoformat(),
                "fim": s.fim.isoformat(),
                "dur_min": s.dur_min,
            }
            for s in res.sessoes
        ],
        "nao_alocado": [vars(n) for n in res.nao_alocado],
        "preferencias_usadas": res.prefs_usadas,
    }
