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
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone

from ..models import Evento
from . import holidays
from .recurrence import expandir

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
# preferência e re-tenta só o que falta.
NIVEIS = (0, 1, 2, 3, 4)
MIN_POR_DIA = 24 * 60


@dataclass
class Preferencias:
    """Preferências efetivas já normalizadas (janelas em minutos do dia)."""

    janela_inicio_min: int
    janela_fim_min: int
    evitar_fds: bool
    max_min_por_dia_por_tarefa: int | None
    max_min_por_dia_total: int | None
    sessao_min: int
    sessao_max: int
    granularidade: int


@dataclass
class _PrefsNivel:
    """Snapshot das preferências para um nível de relaxamento."""

    evitar_fds: bool
    janela_inicio_min: int
    janela_fim_min: int
    max_dia_tarefa: int | None
    max_dia_total: int | None
    sessao_min: int


@dataclass
class TarefaEntrada:
    """Tarefa elegível, já validada pela view (tem deadline/esforço/classe)."""

    id: str
    titulo: str
    classe_id: str
    esforco: int  # minutos
    deadline: datetime


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
        evitar_fds, janela_inicio, janela_fim, max_tarefa, max_total, sessao_min
    )


# --------------------------------------------------------------------------- #
# Eventos ocupados                                                             #
# --------------------------------------------------------------------------- #
def intervalos_ocupados(agora, horizonte_fim):
    """Intervalos [inicio, fim] bloqueados no horizonte, já mesclados.

    Cobre eventos simples (query direta) e recorrentes (expandidos sob demanda,
    igual EventoViewSet.list). Ocorrências PULADAS são omitidas por `expandir`.
    """
    intervalos = []

    simples = Evento.objects.filter(
        regra_recorrencia__isnull=True, inicio__lt=horizonte_fim, fim__gt=agora
    ).only("inicio", "fim")
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


def slots_livres(inicio_busca, fim_busca, pn, granularidade, ocupado):
    """Intervalos livres dentro de [inicio_busca, fim_busca], dia a dia.

    Para cada dia monta a janela [janela_inicio, janela_fim] (horário local),
    pula fim de semana se `pn.evitar_fds`, subtrai `ocupado` e snapa os inícios
    na granularidade. Devolve em ordem cronológica.
    """
    tz = timezone.get_current_timezone()
    slots = []
    dia = timezone.localtime(inicio_busca, tz).date()
    ultimo = timezone.localtime(fim_busca, tz).date()
    while dia <= ultimo:
        if not (pn.evitar_fds and dia.weekday() >= 5):
            meia_noite = _midnight_aware(dia, tz)
            janela_ini = meia_noite + timedelta(minutes=pn.janela_inicio_min)
            janela_fim = meia_noite + timedelta(minutes=pn.janela_fim_min)
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
def calcular_plano(tarefas, ocupado, prefs, agora, horizonte_fim):
    """Aloca sessões para todas as tarefas. Retorna (sessoes, nao_alocado)."""
    sessoes = []
    nao_alocado = []
    ocupado = list(ocupado)  # mutável: cada sessão colocada vira "ocupado"
    min_tarefa_dia = {}  # (tarefa_id, date) -> minutos já alocados
    min_total_dia = {}  # date -> minutos já alocados (todas as tarefas)
    tz = timezone.get_current_timezone()

    # EDF: deadline asc; desempate por menor folga e depois maior esforço.
    tarefas_ord = sorted(
        tarefas,
        key=lambda t: (
            t.deadline,
            (t.deadline - agora) - timedelta(minutes=t.esforco),
            -t.esforco,
        ),
    )

    for tarefa in tarefas_ord:
        if tarefa.deadline <= agora:
            nao_alocado.append(
                NaoAlocado(
                    tarefa.id, tarefa.titulo, tarefa.esforco, "deadline no passado"
                )
            )
            continue

        restante = tarefa.esforco
        fim_busca = min(tarefa.deadline, horizonte_fim)
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
        if pn.max_dia_tarefa is not None:
            limites.append(pn.max_dia_tarefa - min_tarefa_dia.get((tarefa.id, dia), 0))
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
