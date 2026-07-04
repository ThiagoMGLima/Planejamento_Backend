"""Replanejar a partir de agora (Marco C2) — emergências, sem IA.

Congela o passado, devolve ao pool o esforço das sessões futuras aplicadas
(+ tarefas de volta no Inbox via `remarcar`, elegíveis como sempre), re-roda o
solver só do `agora` em diante e responde com plano novo + diff contra o
aplicado ("Cálculo: qua→qui; sábado ganhou 1h"). "Hoje não" (cansaço) é
`dias_bloqueados=[hoje]` — um cenário de um knob só, custo explicitado pelas
métricas.

`replanejar` é PURO (não persiste); persistir é do `aplicar_replanejamento`,
que recalcula dentro da transação (nunca confia num plano enviado pelo
cliente — evita aplicar plano obsoleto).
"""

from dataclasses import dataclass
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from ..models import Evento, Tarefa
from . import aplicacao, cenarios, planejamento


@dataclass
class ResultadoReplanejamento:
    res: planejamento.ResultadoPlano  # plano novo (só do `agora` em diante)
    diff: dict  # por tarefa: movidas/criadas/removidas/inalteradas
    substituiveis: list  # Eventos futuros que o aplicar substitui
    metricas: dict
    metricas_vs_anterior: dict


def _sessoes_futuras(agora):
    """Sessões aplicadas ainda por acontecer (o passado e o concluído congelam)."""
    return list(
        Evento.objects.filter(origem_tarefa__isnull=False, inicio__gte=agora)
        .exclude(status=Evento.Status.CONCLUIDO)
        .select_related("origem_tarefa")
    )


def _pool_e_substituiveis(agora, futuras):
    """Tarefas a replanejar + sessões que serão substituídas.

    - tarefa PROMOVIDA com sessões futuras: esforço = Σ minutos dessas sessões;
    - tarefa INBOX elegível (deadline/esforço/classe — inclui as devolvidas
      pelo `remarcar`): esforço = esforco_estimado, como sempre;
    - tarefa sem deadline/classe não é replanejável: suas sessões ficam como
      estão (fora do pool E fora das substituíveis).
    """
    esforco_futuro = {}
    por_tarefa = {}
    for ev in futuras:
        t = ev.origem_tarefa
        dur = int((ev.fim - ev.inicio).total_seconds() // 60)
        esforco_futuro[t.id] = esforco_futuro.get(t.id, 0) + dur
        por_tarefa[t.id] = t

    pool = []
    replanejaveis = set()
    for tid, t in por_tarefa.items():
        if t.deadline is None or t.classe_id is None:
            continue
        replanejaveis.add(tid)
        if t.status == Tarefa.Status.INBOX:
            continue  # entra pelo ramo do Inbox abaixo, com o esforço total
        pool.append(
            SimpleNamespace(
                id=t.id,
                titulo=t.titulo,
                classe_id=t.classe_id,
                esforco_estimado=esforco_futuro[tid],
                deadline=t.deadline,
            )
        )

    inbox = Tarefa.objects.filter(
        status=Tarefa.Status.INBOX,
        deadline__isnull=False,
        esforco_estimado__gt=0,
        classe__isnull=False,
    )
    pool += list(inbox)

    substituiveis = [ev for ev in futuras if ev.origem_tarefa_id in replanejaveis]
    return pool, substituiveis


def _res_vazio(agora, preferencias):
    prefs, usadas = planejamento.montar_preferencias(preferencias or {})
    return planejamento.ResultadoPlano(
        sessoes=[],
        nao_alocado=[],
        prefs=prefs,
        prefs_usadas=usadas,
        tarefas=[],
        ocupado=[],
        agora=agora,
        horizonte_fim=agora,
    )


def _res_anterior(res, substituiveis, agora):
    """Pseudo-ResultadoPlano das sessões antigas, p/ medir com a mesma régua."""
    sessoes = [
        planejamento.Sessao(
            tarefa_id=str(ev.origem_tarefa_id),
            tarefa_titulo=ev.titulo,
            classe_id=str(ev.classe_id),
            inicio=ev.inicio,
            fim=ev.fim,
            dur_min=int((ev.fim - ev.inicio).total_seconds() // 60),
        )
        for ev in substituiveis
    ]
    sessoes.sort(key=lambda s: s.inicio)
    return planejamento.ResultadoPlano(
        sessoes=sessoes,
        nao_alocado=[],
        prefs=res.prefs,
        prefs_usadas=res.prefs_usadas,
        tarefas=res.tarefas,
        ocupado=[],
        agora=agora,
        horizonte_fim=res.horizonte_fim,
    )


def replanejar(agora=None, dias_bloqueados=None, preferencias=None):
    """Recalcula o plano do `agora` em diante. PURO — não persiste.

    1. sessões futuras aplicadas (origem_tarefa, inicio ≥ agora, ≠ CONCLUIDO);
    2. esforço restante por tarefa = Σ minutos dessas sessões (+ Inbox elegível);
    3. ocupado EXCLUI as sessões do passo 1 (serão substituídas — não podem se
       auto-bloquear);
    4. plano novo = montar_plano(..., diretrizes={"dias_bloqueados": ...});
    5. diff + métricas contra o plano anterior.
    """
    agora = agora or timezone.now()
    futuras = _sessoes_futuras(agora)
    pool, substituiveis = _pool_e_substituiveis(agora, futuras)

    if not pool:
        res = _res_vazio(agora, preferencias)
        vazio = cenarios.metricas_do_plano(res)
        return ResultadoReplanejamento(
            res, {}, [], vazio, cenarios.normalizar(vazio, vazio)
        )

    diretrizes = None
    if dias_bloqueados:
        diretrizes = {
            "dias_bloqueados": [
                d if isinstance(d, str) else d.isoformat() for d in dias_bloqueados
            ]
        }
    res = planejamento.montar_plano(
        pool,
        agora,
        preferencias,
        diretrizes,
        excluir_evento_ids=[ev.id for ev in substituiveis],
        # O esforço do pool já vem em minutos de sessão (não é estimativa):
        # aplicar o fator de classe aqui seria corrigir duas vezes.
        usar_fatores=False,
    )

    anteriores = _res_anterior(res, substituiveis, agora)
    diff = diff_planos(anteriores.sessoes, res.sessoes)
    metricas = cenarios.metricas_do_plano(res)
    metricas_anterior = cenarios.metricas_do_plano(anteriores)
    return ResultadoReplanejamento(
        res=res,
        diff=diff,
        substituiveis=substituiveis,
        metricas=metricas,
        metricas_vs_anterior=cenarios.normalizar(metricas, metricas_anterior),
    )


def diff_planos(sessoes_antigas, sessoes_novas):
    """Diff por tarefa: movidas [(de, para)], criadas, removidas, inalteradas.

    Sessão com (inicio, fim) idêntico é inalterada; as demais são pareadas em
    ordem cronológica (movidas); o excedente vira criada/removida. Alimenta a
    narrativa ("Cálculo: qua→qui") e o front.
    """

    def _iv(s):
        return {"inicio": s.inicio.isoformat(), "fim": s.fim.isoformat()}

    por_tarefa = {}
    for s in sessoes_antigas:
        por_tarefa.setdefault(s.tarefa_id, {"titulo": s.tarefa_titulo}).setdefault(
            "antigas", []
        ).append(s)
    for s in sessoes_novas:
        por_tarefa.setdefault(s.tarefa_id, {"titulo": s.tarefa_titulo}).setdefault(
            "novas", []
        ).append(s)

    diff = {}
    for tid, grupo in por_tarefa.items():
        antigas = sorted(grupo.get("antigas", []), key=lambda s: s.inicio)
        novas = sorted(grupo.get("novas", []), key=lambda s: s.inicio)

        chaves_novas = {(s.inicio, s.fim) for s in novas}
        chaves_antigas = {(s.inicio, s.fim) for s in antigas}
        inalteradas = [s for s in antigas if (s.inicio, s.fim) in chaves_novas]
        resto_antigas = [s for s in antigas if (s.inicio, s.fim) not in chaves_novas]
        resto_novas = [s for s in novas if (s.inicio, s.fim) not in chaves_antigas]

        n_pares = min(len(resto_antigas), len(resto_novas))
        movidas = [
            {"de": _iv(resto_antigas[i]), "para": _iv(resto_novas[i])}
            for i in range(n_pares)
        ]
        diff[tid] = {
            "titulo": grupo["titulo"],
            "movidas": movidas,
            "criadas": [_iv(s) for s in resto_novas[n_pares:]],
            "removidas": [_iv(s) for s in resto_antigas[n_pares:]],
            "inalteradas": [_iv(s) for s in inalteradas],
        }
    return diff


def aplicar_replanejamento(agora=None, dias_bloqueados=None, preferencias=None):
    """Recalcula E persiste, numa transação: remove as sessões futuras
    substituídas e cria as novas (origem_tarefa preservado via aplicar_sessoes).
    Retorna (ResultadoReplanejamento, eventos_criados, eventos_removidos).
    """
    with transaction.atomic():
        rp = replanejar(agora, dias_bloqueados, preferencias)
        removidos = [ev.id for ev in rp.substituiveis]
        Evento.objects.filter(id__in=removidos).delete()
        criados = aplicacao.aplicar_sessoes(
            [
                {"tarefa_id": s.tarefa_id, "inicio": s.inicio, "fim": s.fim}
                for s in rp.res.sessoes
            ]
        )
    return rp, len(criados), len(removidos)
