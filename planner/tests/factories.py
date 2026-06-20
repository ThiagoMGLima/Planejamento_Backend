"""Factories de teste (factory_boy)."""

import datetime

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from planner.models import Classe, Evento, Ocorrencia, RegraRecorrencia, Tarefa


def aware(ano, mes, dia, hora=0, minuto=0):
    """Datetime tz-aware no fuso ativo (America/Sao_Paulo)."""
    return timezone.make_aware(datetime.datetime(ano, mes, dia, hora, minuto))


class ClasseFactory(DjangoModelFactory):
    class Meta:
        model = Classe

    nome = factory.Sequence(lambda n: f"Classe {n}")
    cor = "#abcdef"
    rastreia_conclusao = False


class TarefaFactory(DjangoModelFactory):
    class Meta:
        model = Tarefa

    titulo = factory.Sequence(lambda n: f"Tarefa {n}")
    classe = factory.SubFactory(ClasseFactory)


class RegraRecorrenciaFactory(DjangoModelFactory):
    class Meta:
        model = RegraRecorrencia

    tipo = RegraRecorrencia.Tipo.SEMANAL
    dias = [0]


class EventoFactory(DjangoModelFactory):
    class Meta:
        model = Evento

    titulo = factory.Sequence(lambda n: f"Evento {n}")
    classe = factory.SubFactory(ClasseFactory)
    inicio = factory.LazyFunction(lambda: aware(2026, 6, 1, 8))
    fim = factory.LazyFunction(lambda: aware(2026, 6, 1, 10))
    rastrear_conclusao = False


class OcorrenciaFactory(DjangoModelFactory):
    class Meta:
        model = Ocorrencia

    evento = factory.SubFactory(EventoFactory)
    data = datetime.date(2026, 6, 1)
