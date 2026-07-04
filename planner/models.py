"""Modelo de dados do Planejador de Rotina (Handoff §4).

Desvio deliberado (projeto local/single-user, ver PLAN.md): os models NÃO têm
FK `dono`. Onde o handoff fala em "dono", trate como inexistente — há um único
usuário local. Constraints de unicidade que no handoff eram por-dono passam a
ser globais.
"""

from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import F, Q


class TimestampedModel(models.Model):
    """Base com UUID e auditoria, compartilhada por todas as entidades."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Classe(TimestampedModel):
    """Tipo de atividade. Define cor e o padrão de rastreamento de conclusão."""

    nome = models.CharField(max_length=80, unique=True)
    cor = models.CharField(max_length=7)  # hex, ex.: "#ecf4df"
    rastreia_conclusao = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Classe"
        verbose_name_plural = "Classes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Tarefa(TimestampedModel):
    """Pendência sem horário (Inbox)."""

    class Status(models.TextChoices):
        INBOX = "INBOX", "Inbox"
        PROMOVIDA = "PROMOVIDA", "Promovida"

    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    classe = models.ForeignKey(
        Classe, null=True, blank=True, on_delete=models.SET_NULL, related_name="tarefas"
    )
    # Campos da Fase 2: existem no schema, sem lógica de motor no MVP.
    deadline = models.DateTimeField(null=True, blank=True)
    esforco_estimado = models.PositiveIntegerField(null=True, blank=True)  # minutos
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.INBOX
    )

    class Meta:
        verbose_name = "Tarefa"
        verbose_name_plural = "Tarefas"
        ordering = ["-criado_em"]

    def __str__(self):
        return self.titulo


class RegraRecorrencia(TimestampedModel):
    """Regra de recorrência de um evento (expandida via rrule — Handoff §6)."""

    class Tipo(models.TextChoices):
        SEMANAL = "SEMANAL", "Semanal"  # dias da semana (0=seg … 6=dom)
        MENSAL = "MENSAL", "Mensal"  # dias do mês (1..31)

    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    dias = ArrayField(models.PositiveSmallIntegerField())
    ignorar_feriados = models.BooleanField(default=False)
    data_fim = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Regra de recorrência"
        verbose_name_plural = "Regras de recorrência"

    def __str__(self):
        return f"{self.get_tipo_display()} {self.dias}"


class Evento(TimestampedModel):
    """Item posicionado no calendário."""

    class Status(models.TextChoices):
        AGENDADO = "AGENDADO", "Agendado"
        CONCLUIDO = "CONCLUIDO", "Concluído"
        REMARCADO = "REMARCADO", "Remarcado"
        # PENDENTE é DERIVADO na leitura — nunca gravado (Handoff §5).

    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    inicio = models.DateTimeField()  # tz-aware
    fim = models.DateTimeField()  # tz-aware
    classe = models.ForeignKey(Classe, on_delete=models.PROTECT, related_name="eventos")
    rastrear_conclusao = models.BooleanField()
    status = models.CharField(
        max_length=10, choices=Status.choices, null=True, blank=True
    )
    origem_tarefa = models.ForeignKey(
        Tarefa,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eventos",
    )
    regra_recorrencia = models.ForeignKey(
        RegraRecorrencia,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eventos",
    )

    class Meta:
        verbose_name = "Evento"
        verbose_name_plural = "Eventos"
        indexes = [models.Index(fields=["inicio", "fim"])]  # queries por janela
        constraints = [
            models.CheckConstraint(
                check=Q(fim__gt=F("inicio")), name="ck_evento_fim_apos_inicio"
            )
        ]

    def __str__(self):
        return self.titulo


class PesoPreferencia(TimestampedModel):
    """Peso aprendido de uma métrica de cenário (Marco C1b, §2.4 da visão).

    Aprendido por escolha revelada (EWMA em services/adaptacao.py); 1.0 é o
    neutro. Ordena e sugere cenários, nunca filtra.
    """

    metrica = models.CharField(max_length=40, unique=True)
    valor = models.FloatField(default=1.0)

    class Meta:
        verbose_name = "Peso de preferência"
        verbose_name_plural = "Pesos de preferência"

    def __str__(self):
        return f"{self.metrica}={self.valor:.2f}"


class EscolhaCenario(TimestampedModel):
    """Escolha CRUA de um lote de cenários exibido (Marco C1b).

    Guarda o lote inteiro (com métricas) e qual cenário foi escolhido — permite
    trocar a regra de aprendizado depois e recalcular os pesos do zero.
    """

    lote = models.JSONField()  # todos os cenários exibidos + métricas
    escolhido = models.CharField(max_length=60)  # id do cenário escolhido
    era_sugerido = models.BooleanField()
    pesos_no_momento = models.JSONField()  # auditoria/replay

    class Meta:
        verbose_name = "Escolha de cenário"
        verbose_name_plural = "Escolhas de cenário"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.escolhido} ({self.criado_em:%Y-%m-%d})"


class RegistroExecucao(TimestampedModel):
    """Histórico cru de execução (Marco C3) — base dos fatores adaptativos.

    Escrito pelos fluxos `concluir`/`remarcar` de services/completion.py.
    `real_min` é opcional (o usuário informa se quiser); sem ele o registro
    ainda vale para o score de flexibilidade (taxa de remarcação).
    """

    tarefa = models.ForeignKey(
        Tarefa,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="execucoes",
    )
    evento = models.ForeignKey(
        Evento,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="execucoes",
    )
    classe = models.ForeignKey(
        Classe,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="execucoes",
    )
    planejado_min = models.PositiveIntegerField(null=True, blank=True)
    real_min = models.PositiveIntegerField(null=True, blank=True)
    remarcado = models.BooleanField(default=False)
    concluido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Registro de execução"
        verbose_name_plural = "Registros de execução"
        ordering = ["-criado_em"]

    def __str__(self):
        acao = "remarcado" if self.remarcado else "concluído"
        return f"{acao} ({self.criado_em:%Y-%m-%d})"


class Ocorrencia(TimestampedModel):
    """Materialização de uma data de um evento recorrente (Handoff §4.5).

    Existe só quando o usuário toca aquela ocorrência (conclui, remarca, pula
    ou reagenda só ela). Ocorrências não tocadas são virtuais.
    """

    evento = models.ForeignKey(
        Evento, on_delete=models.CASCADE, related_name="ocorrencias"
    )
    data = models.DateField()  # a data específica desta ocorrência
    inicio_override = models.DateTimeField(null=True, blank=True)
    fim_override = models.DateTimeField(null=True, blank=True)
    status_override = models.CharField(
        max_length=10, null=True, blank=True
    )  # CONCLUIDO / REMARCADO / PULADO

    class Meta:
        verbose_name = "Ocorrência"
        verbose_name_plural = "Ocorrências"
        constraints = [
            models.UniqueConstraint(
                fields=["evento", "data"], name="uq_ocorrencia_evento_data"
            )
        ]

    def __str__(self):
        return f"{self.evento.titulo} @ {self.data}"
