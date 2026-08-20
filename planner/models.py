"""Modelo de dados do Planejador de Rotina (Handoff §4).

**Fase 0B / PR1:** os models-raiz passaram a ter FK `dono` e o acesso global
deixou de ser o default — ver `planner/managers.py`. As constraints que eram
globais (`Classe.nome`, `PesoPreferencia.metrica`, `FeriadoLocal`) viraram
por-dono. Só `Ocorrencia` fica sem `dono`: herda pelo `evento` (CASCADE,
não-nulo), então não há como uma ocorrência existir fora do dono do evento.
"""

from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
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

    class Estrategia(models.TextChoices):
        CEDO = "CEDO", "O quanto antes"
        TARDE = "TARDE", "O mais perto possível do prazo"

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

    # --- Parâmetros de agendamento (Fase 1.1) --------------------------------
    # Ocultos: o usuário não digita nada disto no frontend. Saem na API (D4) para
    # o agente/MCP alcançarem pelo caminho normal.
    #
    # São ORTOGONAIS por decisão de desenho: cada campo expressa UMA condição e
    # não altera o significado dos outros. Em particular `estrategia=TARDE` NÃO
    # aciona `buffer_dias` — "terminar na véspera" é `nao_depois_de`.
    #
    # NÃO há default herdado de classe (D2): `estrategia` nulo é "nada foi dito",
    # e o solver trata como CEDO. Ver docs/tasks/fase1-parametros-por-tarefa.md.
    estrategia = models.CharField(
        max_length=5, choices=Estrategia.choices, null=True, blank=True
    )
    # Piso e teto DUROS de data: a cascata de relaxamento do solver nunca os
    # afrouxa. Relaxar "não antes de X" seria desfazer o pedido, não degradá-lo —
    # se não couber, a tarefa cai em `nao_alocado` com motivo.
    nao_antes_de = models.DateField(null=True, blank=True)
    nao_depois_de = models.DateField(null=True, blank=True)
    # Restrições SUAVES: compõem com as preferências globais pelo mais restritivo
    # e caem no nível ≥ 3 do relaxamento, junto com os overrides de janela.
    janela_inicio = models.TimeField(null=True, blank=True)
    janela_fim = models.TimeField(null=True, blank=True)
    dias_permitidos = ArrayField(  # 0=seg … 6=dom, mesma convenção de RegraRecorrencia
        models.PositiveSmallIntegerField(), null=True, blank=True
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
    # Identidade estável do que `importar_planejamento_ensino` criou (Fase 1.2 / PR B).
    # Sem ela o comando só teria o título para se reconhecer, e aí renomear a
    # disciplina pelo app faria a próxima importação criar uma SEGUNDA série — duas
    # aulas no mesmo horário, em silêncio. Vazio em tudo que não veio de importação.
    chave_importacao = models.CharField(max_length=100, blank=True)

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
            ),
            # Parcial: o vazio é o caso comum (todo evento não-importado) e não pode
            # colidir consigo mesmo. Por-dono, como toda unicidade daqui (0B.5).
            models.UniqueConstraint(
                fields=["dono", "chave_importacao"],
                condition=~Q(chave_importacao=""),
                name="uq_evento_dono_chave_importacao",
            ),
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
    """O que uma data específica de um evento recorrente tem de diferente.

    **Duas razões para existir (a segunda entrou na Fase 1.2):**

    1. o usuário TOCOU aquela data — concluiu, remarcou, pulou ou reagendou só
       ela (`*_override` de horário e status);
    2. aquela data tem CONTEÚDO próprio — a aula de 20/10 é a prova, a de 26/11
       é quando o trabalho é entregue (`titulo/descricao/classe_override`).

    A (2) desfaz a leitura antiga de que "ocorrência só existe quando o usuário
    toca". A série continua expandida sob demanda (nada materializa calendário),
    mas um semestre importado deixa ~18 linhas por disciplina aqui, escritas por
    `importar_planejamento_ensino` sem ninguém ter clicado em nada.

    Semântica de todo override: **preenchido substitui, vazio herda a série**.
    `descricao_override` SUBSTITUI e não concatena — a descrição do evento vale o
    semestre inteiro (ementa, professor, critério), a da ocorrência vale o dia.
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

    # --- Conteúdo do dia (Fase 1.2) ------------------------------------------
    # Resolvidos na LEITURA (services/recurrence.montar_ocorrencia), nunca
    # copiados para dentro do Evento: a série continua sendo a fonte do que não
    # varia. Quem consome o payload não precisa saber de onde o valor veio.
    titulo_override = models.CharField(max_length=200, blank=True)
    descricao_override = models.TextField(blank=True)
    # PROTECT como `Evento.classe`, e pelo mesmo motivo: apagar a classe "Prova"
    # com dias de prova pendurados nela tem de doer, não sumir em silêncio.
    classe_override = models.ForeignKey(
        Classe,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocorrencias_override",
    )

    class Meta:
        verbose_name = "Ocorrência"
        verbose_name_plural = "Ocorrências"
        constraints = [
            models.UniqueConstraint(
                fields=["evento", "data"], name="uq_ocorrencia_evento_data"
            )
        ]

    def clean(self):
        """A classe do override tem de ser do MESMO dono do evento.

        `Ocorrencia` não tem `dono` para o manager escopar, e o `classe_override`
        é a primeira FK daqui para um model por-dono — sem esta checagem, seria
        o caminho por onde a classe de um perfil apareceria no calendário de
        outro. Vale onde `full_clean` roda (admin e importador); a guarda de
        verdade continua sendo o escopo de quem monta a query.
        """
        super().clean()
        if (
            self.classe_override_id
            and self.evento_id
            and self.classe_override.dono_id != self.evento.dono_id
        ):
            raise ValidationError(
                {"classe_override": "A classe deve ser do mesmo dono do evento."}
            )

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
