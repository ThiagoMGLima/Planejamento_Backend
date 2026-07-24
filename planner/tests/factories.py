"""Factories de teste (factory_boy).

**Dono nas factories (Fase 0B, PR1).** Todo model-raiz agora exige `dono`. Para
não reescrever as ~3.800 linhas de teste que já existiam, o default é um perfil
único e compartilhado (`PerfilFactory` com `django_get_or_create` no e-mail):
os testes antigos continuam falando de "os dados", e o dono é implícito e o
mesmo — exatamente o mundo em que eles foram escritos.

Testar isolamento é o oposto disso: exige passar `dono=` explicitamente, com
dois perfis. Ver `test_isolamento.py` — é lá que dois donos aparecem, e é o
único lugar onde esses testes provam alguma coisa.
"""

import datetime

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from planner.models import (
    Classe,
    Evento,
    Ocorrencia,
    Perfil,
    RegraRecorrencia,
    Tarefa,
)
from planner.services import perfis


def aware(ano, mes, dia, hora=0, minuto=0):
    """Datetime tz-aware no fuso ativo (America/Sao_Paulo)."""
    return timezone.make_aware(datetime.datetime(ano, mes, dia, hora, minuto))


class PerfilFactory(DjangoModelFactory):
    class Meta:
        model = Perfil
        # Sem sequência no e-mail de propósito: chamadas repetidas devolvem o
        # MESMO perfil, então `TarefaFactory()` e `EventoFactory()` de um teste
        # antigo caem no mesmo dono e continuam se enxergando.
        django_get_or_create = ("email",)

    # É o **perfil local** — o mesmo que `perfil_do_request` devolve enquanto
    # não há login. Sem isso, um teste de API criaria dados via factory num
    # perfil e leria pela API de outro, e todos os testes de leitura falhariam
    # por um motivo que não tem nada a ver com o que eles testam.
    id = perfis.UUID_PERFIL_LOCAL
    email = perfis.EMAIL_PERFIL_LOCAL
    nome = "Perfil local"


class OutroPerfilFactory(PerfilFactory):
    """O segundo perfil dos testes de isolamento — o "outro usuário".

    Nunca é quem a API resolve: é sempre o lado de fora da fronteira.
    """

    id = "00000000-0000-0000-0000-0000000000ff"
    email = "outro@planejador.local"
    nome = "Outro perfil"


class ClasseFactory(DjangoModelFactory):
    class Meta:
        model = Classe

    dono = factory.SubFactory(PerfilFactory)
    nome = factory.Sequence(lambda n: f"Classe {n}")
    cor = "#abcdef"
    rastreia_conclusao = False


class TarefaFactory(DjangoModelFactory):
    class Meta:
        model = Tarefa

    dono = factory.SubFactory(PerfilFactory)
    titulo = factory.Sequence(lambda n: f"Tarefa {n}")
    # A classe nasce do MESMO dono da tarefa: passar `dono=outro` a uma factory
    # não pode gerar, sem ninguém pedir, um objeto cruzando perfis.
    classe = factory.SubFactory(ClasseFactory, dono=factory.SelfAttribute("..dono"))


class RegraRecorrenciaFactory(DjangoModelFactory):
    class Meta:
        model = RegraRecorrencia

    dono = factory.SubFactory(PerfilFactory)
    tipo = RegraRecorrencia.Tipo.SEMANAL
    dias = [0]


class EventoFactory(DjangoModelFactory):
    class Meta:
        model = Evento

    dono = factory.SubFactory(PerfilFactory)
    titulo = factory.Sequence(lambda n: f"Evento {n}")
    classe = factory.SubFactory(ClasseFactory, dono=factory.SelfAttribute("..dono"))
    inicio = factory.LazyFunction(lambda: aware(2026, 6, 1, 8))
    fim = factory.LazyFunction(lambda: aware(2026, 6, 1, 10))
    rastrear_conclusao = False


class OcorrenciaFactory(DjangoModelFactory):
    class Meta:
        model = Ocorrencia

    evento = factory.SubFactory(EventoFactory)
    data = datetime.date(2026, 6, 1)
