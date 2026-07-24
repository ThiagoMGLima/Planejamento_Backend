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
from planner.services import adaptacao, perfis, planejamento

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _cache_limpo():
    # fator_classe é cacheado (TTL curto) — um teste não pode enxergar o fator
    # neutro/viciado calculado por outro.
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def test_seed_demo_cria_dataset_completo(perfil):
    call_command("seed_demo")
    assert Tarefa.objects.do_dono(perfil).count() >= 20
    assert Evento.objects.do_dono(perfil).count() >= 25
    assert RegraRecorrencia.objects.do_dono(perfil).count() >= 8
    assert Ocorrencia.objects.filter(evento__dono=perfil).count() >= 3
    assert RegistroExecucao.objects.do_dono(perfil).count() >= 14


def test_seed_demo_clear_e_idempotente(perfil):
    call_command("seed_demo")
    call_command("seed_demo", "--clear")
    call_command("seed_demo", "--clear")
    primeira = Tarefa.objects.do_dono(perfil).count()
    call_command("seed_demo", "--clear")
    assert Tarefa.objects.do_dono(perfil).count() == primeira  # não acumula com --clear
    # E as classes padrão sobrevivem ao clear.
    assert Classe.objects.do_dono(perfil).count() >= 5


def test_seed_demo_sem_classes_padrao_falha_com_mensagem(perfil):
    Evento.objects.do_dono(perfil).delete()
    Tarefa.objects.do_dono(perfil).delete()
    Classe.objects.do_dono(perfil).delete()
    with pytest.raises(CommandError, match="Classes padrão ausentes"):
        call_command("seed_demo")


def test_seed_demo_tarefas_elegiveis_passam_no_validador_do_solver(perfil):
    call_command("seed_demo")
    elegiveis = Tarefa.objects.do_dono(perfil).filter(
        status=Tarefa.Status.INBOX,
        deadline__isnull=False,
        deadline__gt=timezone.now(),
        esforco_estimado__isnull=False,
        classe__isnull=False,
    )
    assert elegiveis.count() >= 12
    validas, invalidas = planejamento.validar_tarefas(
        perfil, [str(t.id) for t in elegiveis]
    )
    assert not invalidas


def test_seed_demo_cobre_os_edge_cases_prometidos(perfil):
    call_command("seed_demo")
    # Deadline no passado (pendência), esforço gigante e esforço de 15min.
    assert Tarefa.objects.do_dono(perfil).filter(deadline__lt=timezone.now()).exists()
    assert Tarefa.objects.do_dono(perfil).filter(esforco_estimado__gte=600).exists()
    assert Tarefa.objects.do_dono(perfil).filter(esforco_estimado=15).exists()
    # Inelegíveis: sem deadline, sem esforço, sem classe.
    inbox = Tarefa.objects.do_dono(perfil).filter(status=Tarefa.Status.INBOX)
    assert inbox.filter(deadline__isnull=True).exists()
    assert inbox.filter(esforco_estimado__isnull=True).exists()
    assert inbox.filter(classe__isnull=True).exists()
    # Evento atravessando a meia-noite.
    cruza = [
        e
        for e in Evento.objects.do_dono(perfil)
        if timezone.localtime(e.inicio).date() != timezone.localtime(e.fim).date()
    ]
    assert cruza
    # Sobreposição deliberada: dois eventos exatamente no mesmo horário.
    from collections import Counter

    pares = Counter(
        (e.inicio, e.fim)
        for e in Evento.objects.do_dono(perfil).filter(regra_recorrencia__isnull=True)
    )
    assert any(n >= 2 for n in pares.values())
    # Promovidas com sessões vinculadas.
    assert (
        Evento.objects.do_dono(perfil).filter(origem_tarefa__isnull=False).count() >= 3
    )


def test_seed_demo_alimenta_os_fatores_adaptativos(perfil):
    call_command("seed_demo")
    estudar = Classe.objects.do_dono(perfil).get(nome="Estudar")
    basicas = Classe.objects.do_dono(perfil).get(nome="Tarefas básicas")
    trabalho = Classe.objects.do_dono(perfil).get(nome="Trabalho")
    # O viés proposital do histórico precisa aparecer nos fatores:
    assert adaptacao.fator_classe(perfil, estudar.id) > 1.05  # subestima
    assert adaptacao.fator_classe(perfil, basicas.id) < 1.0  # superestima
    assert 0.9 <= adaptacao.fator_classe(perfil, trabalho.id) <= 1.1  # calibrado
    # Flexibilidade: básicas remarca mais que estudar.
    assert adaptacao.flexibilidade_classe(
        perfil, basicas.id
    ) > adaptacao.flexibilidade_classe(perfil, estudar.id)


def test_seed_planejamento_continua_funcionando(perfil):
    call_command("seed_planejamento", "--clear")
    assert (
        Tarefa.objects.do_dono(perfil).filter(status=Tarefa.Status.INBOX).count() >= 10
    )
    assert Evento.objects.do_dono(perfil).count() >= 5


def test_seed_demo_montar_plano_de_ponta_a_ponta(perfil):
    """O dataset inteiro passa pelo solver sem explodir e aloca sessões."""
    call_command("seed_demo")
    elegiveis = Tarefa.objects.do_dono(perfil).filter(
        status=Tarefa.Status.INBOX,
        deadline__isnull=False,
        deadline__gt=timezone.now() + timedelta(hours=12),
        esforco_estimado__isnull=False,
        classe__isnull=False,
    )
    validas, invalidas = planejamento.validar_tarefas(
        perfil, [str(t.id) for t in elegiveis]
    )
    assert not invalidas
    res = planejamento.montar_plano(perfil, validas, timezone.now(), {})
    assert res.sessoes, "solver não alocou nenhuma sessão do seed"


# --------------------------------------------------------------------------- #
# Seed das classes padrão por perfil (0B.6) e --dono nos comandos              #
# --------------------------------------------------------------------------- #
def test_perfil_novo_nasce_com_as_5_classes_padrao(outro_perfil):
    """Antes do PR1 as 5 classes vinham da migration 0002, globais — o migrate
    não sabia para quem semear. Agora é por perfil, na criação: sem isso uma
    conta nova abriria o app sem classe nenhuma, e `Evento.classe` é
    obrigatório, então ela não conseguiria criar nada."""
    criadas = perfis.seed_classes_padrao(outro_perfil)
    assert len(criadas) == 5
    assert Classe.objects.do_dono(outro_perfil).count() == 5

    # Idempotente: chamar de novo não duplica.
    perfis.seed_classes_padrao(outro_perfil)
    assert Classe.objects.do_dono(outro_perfil).count() == 5


def test_classes_padrao_de_perfis_diferentes_nao_colidem(perfil, outro_perfil):
    perfis.seed_classes_padrao(outro_perfil)
    nomes_meus = set(Classe.objects.do_dono(perfil).values_list("nome", flat=True))
    nomes_dele = set(
        Classe.objects.do_dono(outro_perfil).values_list("nome", flat=True)
    )
    assert nomes_meus == nomes_dele  # mesmos nomes...
    assert Classe.objects.sem_escopo().count() == 10  # ...e linhas distintas


def test_seed_com_dono_escreve_so_no_perfil_indicado(perfil, outro_perfil):
    perfis.seed_classes_padrao(outro_perfil)
    call_command("seed_demo", "--dono", outro_perfil.email)

    assert Tarefa.objects.do_dono(outro_perfil).count() >= 20
    assert Tarefa.objects.do_dono(perfil).count() == 0  # o meu perfil ficou intocado


def test_seed_clear_nao_apaga_dados_de_outro_perfil(perfil, outro_perfil):
    call_command("seed_demo")  # perfil local
    meus_antes = Tarefa.objects.do_dono(perfil).count()

    perfis.seed_classes_padrao(outro_perfil)
    call_command("seed_demo", "--dono", outro_perfil.email, "--clear")

    assert Tarefa.objects.do_dono(perfil).count() == meus_antes


def test_seed_com_dono_inexistente_falha_com_mensagem(perfil):
    with pytest.raises(CommandError, match="Perfil não encontrado"):
        call_command("seed_demo", "--dono", "ninguem@exemplo.com")
