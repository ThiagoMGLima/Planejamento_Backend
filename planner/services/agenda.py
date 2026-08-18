"""Leitura da agenda: janela expandida e pendentes (PR0 da Fase 0B).

Estava dentro de `EventoViewSet.list` e da view `pendentes`. Desceu para cá pelo
mesmo motivo de `services/tarefas.py`: as ferramentas do agente precisam disto
em processo, e a view devia ser fina.

**Estes services devolvem objetos de domínio, não DTOs.** É de propósito: assim
`services/` não importa `serializers` (que é camada de apresentação), e cada
consumidor monta a forma de que precisa — a view monta o JSON do contrato, o
agente monta o resumo pré-digerido que o modelo consegue copiar sem alucinar.
"""

from collections import namedtuple

from planner.models import Evento
from planner.services import holidays
from planner.services.planejamento import JANELA_MAX
from planner.services.recurrence import expandir, prefetch_ocorrencias

# `ocorrencia` é None em evento não recorrente; senão é a view devolvida por
# `recurrence.expandir` (inicio/fim/status/data/persistida já resolvidos).
ItemAgenda = namedtuple("ItemAgenda", "evento ocorrencia")


class JanelaInvalida(ValueError):
    """Janela aberta, invertida ou maior que o teto."""


def validar_janela(inicio, fim):
    """Regra de domínio da janela (Handoff §8.3). Levanta `JanelaInvalida`."""
    if fim <= inicio:
        raise JanelaInvalida("fim deve ser maior que inicio.")
    if fim - inicio > JANELA_MAX:
        raise JanelaInvalida("Janela máxima de ~92 dias.")


def feriados_da_janela(inicio, fim, dono):
    """União dos feriados de todos os anos que a janela cruza."""
    feriados = set()
    for ano in range(inicio.year, fim.year + 1):
        feriados |= holidays.feriados_do_ano(ano, dono)
    return feriados


def eventos_na_janela(dono, inicio, fim):
    """Eventos do perfil que cruzam a janela, com os recorrentes já expandidos.

    Devolve `list[ItemAgenda]` ordenada pelo início efetivo (o da ocorrência,
    quando houver). Ocorrências não tocadas seguem virtuais — a expansão é sob
    demanda, nunca materializa série.
    """
    validar_janela(inicio, fim)
    feriados = feriados_da_janela(inicio, fim, dono)

    itens = [
        ItemAgenda(ev, None)
        for ev in Evento.objects.do_dono(dono)
        .filter(regra_recorrencia__isnull=True, inicio__lt=fim, fim__gt=inicio)
        .select_related("classe", "origem_tarefa")
    ]

    recorrentes = (
        Evento.objects.do_dono(dono)
        .filter(regra_recorrencia__isnull=False)
        .select_related("classe", "regra_recorrencia", "origem_tarefa")
        .prefetch_related(prefetch_ocorrencias())
    )
    for ev in recorrentes:
        itens.extend(
            ItemAgenda(ev, view) for view in expandir(ev, inicio, fim, feriados)
        )

    itens.sort(key=inicio_efetivo)
    return itens


def inicio_efetivo(item):
    """Início que vale para o item: o da ocorrência, se houver."""
    return item.ocorrencia.inicio if item.ocorrencia else item.evento.inicio


def pendentes(dono, agora):
    """Eventos rastreáveis do perfil cujo `status_efetivo` é PENDENTE (§8.4).

    PENDENTE é calculado, nunca gravado: filtramos pelas condições que o
    derivam (rastreável, ainda AGENDADO, `agora` já passou do fim). Cobre
    eventos não recorrentes; ocorrências recorrentes pendentes dependem de
    janela (ver `eventos_na_janela`).
    """
    return (
        Evento.objects.do_dono(dono)
        .filter(
            regra_recorrencia__isnull=True,
            rastrear_conclusao=True,
            status=Evento.Status.AGENDADO,
            fim__lt=agora,
        )
        .select_related("classe", "origem_tarefa")
        .order_by("fim")
    )
