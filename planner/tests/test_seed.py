"""Testes dos comandos de seed — garantem que o dataset demo é utilizável.

O seed é a porta de entrada de quem sobe o projeto: se ele quebrar (ou gerar
dados que o planejador/fatores não conseguem consumir), a demo morre na praia.
Cobre também os edge cases deliberados que o seed promete no docstring.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from planner.models import (
    Classe,
    Evento,
    Ocorrencia,
    RegistroExecucao,
    RegraRecorrencia,
    Tarefa,
)
from planner.services import adaptacao, planejamento

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _cache_limpo():
    # fator_classe é cacheado (TTL curto) — um teste não pode enxergar o fator
    # neutro/viciado calculado por outro.
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def test_seed_demo_cria_dataset_completo():
    call_command("seed_demo")
    assert Tarefa.objects.count() >= 20
    assert Evento.objects.count() >= 25
    assert RegraRecorrencia.objects.count() >= 8
    assert Ocorrencia.objects.count() >= 3
    assert RegistroExecucao.objects.count() >= 14


def test_seed_demo_clear_e_idempotente():
    call_command("seed_demo")
    call_command("seed_demo", "--clear")
    call_command("seed_demo", "--clear")
    primeira = Tarefa.objects.count()
    call_command("seed_demo", "--clear")
    assert Tarefa.objects.count() == primeira  # não acumula com --clear
    # E as classes padrão sobrevivem ao clear.
    assert Classe.objects.count() >= 5


def test_seed_demo_sem_classes_padrao_falha_com_mensagem():
    Evento.objects.all().delete()
    Tarefa.objects.all().delete()
    Classe.objects.all().delete()
    with pytest.raises(CommandError, match="Classes padrão ausentes"):
        call_command("seed_demo")


def test_seed_demo_tarefas_elegiveis_passam_no_validador_do_solver():
    call_command("seed_demo")
    elegiveis = Tarefa.objects.filter(
        status=Tarefa.Status.INBOX,
        deadline__isnull=False,
        deadline__gt=timezone.now(),
        esforco_estimado__isnull=False,
        classe__isnull=False,
    )
    assert elegiveis.count() >= 12
    validas, invalidas = planejamento.validar_tarefas([str(t.id) for t in elegiveis])
    assert not invalidas


def test_seed_demo_cobre_os_edge_cases_prometidos():
    call_command("seed_demo")
    # Deadline no passado (pendência), esforço gigante e esforço de 15min.
    assert Tarefa.objects.filter(deadline__lt=timezone.now()).exists()
    assert Tarefa.objects.filter(esforco_estimado__gte=600).exists()
    assert Tarefa.objects.filter(esforco_estimado=15).exists()
    # Inelegíveis: sem deadline, sem esforço, sem classe.
    inbox = Tarefa.objects.filter(status=Tarefa.Status.INBOX)
    assert inbox.filter(deadline__isnull=True).exists()
    assert inbox.filter(esforco_estimado__isnull=True).exists()
    assert inbox.filter(classe__isnull=True).exists()
    # Evento atravessando a meia-noite.
    cruza = [
        e
        for e in Evento.objects.all()
        if timezone.localtime(e.inicio).date() != timezone.localtime(e.fim).date()
    ]
    assert cruza
    # Sobreposição deliberada: dois eventos exatamente no mesmo horário.
    from collections import Counter

    pares = Counter(
        (e.inicio, e.fim) for e in Evento.objects.filter(regra_recorrencia__isnull=True)
    )
    assert any(n >= 2 for n in pares.values())
    # Promovidas com sessões vinculadas.
    assert Evento.objects.filter(origem_tarefa__isnull=False).count() >= 3


def test_seed_demo_alimenta_os_fatores_adaptativos():
    call_command("seed_demo")
    estudar = Classe.objects.get(nome="Estudar")
    basicas = Classe.objects.get(nome="Tarefas básicas")
    trabalho = Classe.objects.get(nome="Trabalho")
    # O viés proposital do histórico precisa aparecer nos fatores:
    assert adaptacao.fator_classe(estudar.id) > 1.05  # subestima
    assert adaptacao.fator_classe(basicas.id) < 1.0  # superestima
    assert 0.9 <= adaptacao.fator_classe(trabalho.id) <= 1.1  # calibrado
    # Flexibilidade: básicas remarca mais que estudar.
    assert adaptacao.flexibilidade_classe(basicas.id) > adaptacao.flexibilidade_classe(
        estudar.id
    )


def test_seed_planejamento_continua_funcionando():
    call_command("seed_planejamento", "--clear")
    assert Tarefa.objects.filter(status=Tarefa.Status.INBOX).count() >= 10
    assert Evento.objects.count() >= 5


def test_seed_demo_montar_plano_de_ponta_a_ponta():
    """O dataset inteiro passa pelo solver sem explodir e aloca sessões."""
    call_command("seed_demo")
    elegiveis = Tarefa.objects.filter(
        status=Tarefa.Status.INBOX,
        deadline__isnull=False,
        deadline__gt=timezone.now() + timedelta(hours=12),
        esforco_estimado__isnull=False,
        classe__isnull=False,
    )
    validas, invalidas = planejamento.validar_tarefas([str(t.id) for t in elegiveis])
    assert not invalidas
    res = planejamento.montar_plano(validas, timezone.now(), {})
    assert res.sessoes, "solver não alocou nenhuma sessão do seed"
