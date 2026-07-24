"""Modelo de dados do Planejador de Rotina (Handoff §4).

**Fase 0B / PR1:** os models-raiz passaram a ter FK `dono` e o acesso global
deixou de ser o default — ver `planner/managers.py`. As constraints que eram
globais (`Classe.nome`, `PesoPreferencia.metrica`, `FeriadoLocal`) viraram
por-dono. Só `Ocorrencia` fica sem `dono`: herda pelo `evento` (CASCADE,
não-nulo), então não há como uma ocorrência existir fora do dono do evento.
"""

from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import F, Q

from .managers import EscopoManager


class TimestampedModel(models.Model):
    """Base com UUID e auditoria, compartilhada por todas as entidades."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Perfil(TimestampedModel):
    """Uma conta. É o `dono` de tudo que os outros models guardam (0B.3).

    A PK é um UUID **local**, e o id do Supabase mora em `supabase_id`, à parte
    (decisão Q3 do plano). O motivo é prático: 8 FKs apontam para esta PK, então
    trocá-la no PR2 significaria reescrever 8 tabelas com dados dentro —
    enquanto preencher uma coluna é um UPDATE. No PR2 o primeiro login de um
    perfil existente grava aqui o `supabase_id` e a conta local vira a conta
    autenticada, sem migração de dados (decisão Q4).

    `plano`/`trial_ate` existem para o gate de pagamento do PR3 (`pode_usar`);
    aqui são só campos.
    """

    class Plano(models.TextChoices):
        DEMO = "DEMO", "Demo"  # conta de vitrine (PR3), dados semeados
        TRIAL = "TRIAL", "Trial"
        PRO = "PRO", "Pro"

    # Nulo até o PR2; `unique` mesmo assim — dois perfis não podem apontar para
    # o mesmo usuário do Supabase (Postgres não conta NULLs como duplicata).
    supabase_id = models.UUIDField(null=True, blank=True, unique=True)
    email = models.EmailField(unique=True)
    nome = models.CharField(max_length=120, blank=True)
    plano = models.CharField(max_length=8, choices=Plano.choices, default=Plano.TRIAL)
    trial_ate = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Perfil"
        verbose_name_plural = "Perfis"
        ordering = ["email"]

    def __str__(self):
        return self.nome or self.email


class Classe(TimestampedModel):
    """Tipo de atividade. Define cor e o padrão de rastreamento de conclusão."""

    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="classes")
    nome = models.CharField(max_length=80)
    cor = models.CharField(max_length=7)  # hex, ex.: "#ecf4df"
    rastreia_conclusao = models.BooleanField(default=False)

    objects = EscopoManager()

    class Meta:
        verbose_name = "Classe"
        verbose_name_plural = "Classes"
        ordering = ["nome"]
        constraints = [
            # Era `unique=True` global: o 2º usuário não conseguiria ter "Estudar".
            models.UniqueConstraint(fields=["dono", "nome"], name="uq_classe_dono_nome")
        ]

    def __str__(self):
        return self.nome


class Tarefa(TimestampedModel):
    """Pendência sem horário (Inbox)."""

    class Status(models.TextChoices):
        INBOX = "INBOX", "Inbox"
        PROMOVIDA = "PROMOVIDA", "Promovida"

    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="tarefas")
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

    objects = EscopoManager()

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

    # `Evento` aponta para a regra, não o contrário — não há caminho até o dono
    # pelas FKs, então a regra carrega o dela.
    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="regras")
    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    dias = ArrayField(models.PositiveSmallIntegerField())
    ignorar_feriados = models.BooleanField(default=False)
    data_fim = models.DateField(null=True, blank=True)

    objects = EscopoManager()

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

    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="eventos")
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

    objects = EscopoManager()

    class Meta:
        verbose_name = "Evento"
        verbose_name_plural = "Eventos"
        # Toda query por janela agora filtra por dono antes do intervalo.
        indexes = [
            models.Index(fields=["dono", "inicio", "fim"], name="ix_evento_dono_janela")
        ]
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

    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="pesos")
    metrica = models.CharField(max_length=40)
    valor = models.FloatField(default=1.0)

    objects = EscopoManager()

    class Meta:
        verbose_name = "Peso de preferência"
        verbose_name_plural = "Pesos de preferência"
        constraints = [
            # Era `unique=True` global — o 1º a gravar um peso travava o
            # aprendizado de todos os outros perfis.
            models.UniqueConstraint(
                fields=["dono", "metrica"], name="uq_peso_dono_metrica"
            )
        ]

    def __str__(self):
        return f"{self.metrica}={self.valor:.2f}"


class EscolhaCenario(TimestampedModel):
    """Escolha CRUA de um lote de cenários exibido (Marco C1b).

    Guarda o lote inteiro (com métricas) e qual cenário foi escolhido — permite
    trocar a regra de aprendizado depois e recalcular os pesos do zero.
    """

    # Só tem JSONField — nenhuma FK por onde herdar o dono.
    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="escolhas")
    lote = models.JSONField()  # todos os cenários exibidos + métricas
    escolhido = models.CharField(max_length=60)  # id do cenário escolhido
    era_sugerido = models.BooleanField()
    pesos_no_momento = models.JSONField()  # auditoria/replay

    objects = EscopoManager()

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

    # `tarefa`/`evento`/`classe` são os três nulos e SET_NULL: apagados a tarefa
    # e o evento, o registro fica órfão e sem caminho nenhum até o dono. Por isso
    # carrega o seu.
    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="execucoes")
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

    objects = EscopoManager()

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


class FeriadoLocal(TimestampedModel):
    """Feriado municipal/local mantido à mão (Marco C8).

    Não há API confiável para os 5570 municípios; a lista local, editável no
    admin, é a fonte municipal. `ano` nulo = recorre todo ano (ex.: padroeira);
    preenchido = pontual (ex.: feriado decretado uma vez).
    """

    dono = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="feriados")
    nome = models.CharField(max_length=120)
    dia = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()
    ano = models.PositiveIntegerField(null=True, blank=True)

    objects = EscopoManager()

    class Meta:
        verbose_name = "Feriado local"
        verbose_name_plural = "Feriados locais"
        ordering = ["mes", "dia"]
        constraints = [
            models.CheckConstraint(
                check=Q(dia__gte=1, dia__lte=31, mes__gte=1, mes__lte=12),
                name="ck_feriadolocal_data_valida",
            ),
            # Por-dono (decisão Q5): cada perfil mantém a própria lista municipal.
            # Um catálogo global com campo de município fica para quando houver
            # seleção de município — hoje ela não existe.
            models.UniqueConstraint(
                fields=["dono", "dia", "mes", "ano"], name="uq_feriadolocal_dono_data"
            ),
        ]

    def __str__(self):
        quando = f"{self.dia:02d}/{self.mes:02d}" + (f"/{self.ano}" if self.ano else "")
        return f"{self.nome} ({quando})"
