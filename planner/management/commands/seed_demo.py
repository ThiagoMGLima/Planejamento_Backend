"""Semeia um dataset de demonstração realista (semestre de um estudante que estagia).

As 5 classes padrão já vêm da migration 0002; aqui criamos um retrato crível
ancorado em "hoje": grade de aulas + estágio + academia + inglês como rotina
recorrente (o "ocupado" do planejador), um Inbox cheio com prazos e esforços
variados, ~4 semanas de histórico (eventos concluídos/pendentes/remarcados e
REGISTROS DE EXECUÇÃO que alimentam os fatores adaptativos do Marco C3) e
edge cases deliberados de UI/solver:

- deadline hoje à noite, amanhã cedo, no domingo, no passado e a 2 meses;
- esforço gigante com prazo curto (força relaxamento/nao_alocado) e de 15min;
- título longo e título com acento/emoji;
- tarefas inelegíveis (sem prazo / sem esforço / sem classe / sem nada);
- evento atravessando a meia-noite, evento-dia-inteiro, dois eventos no MESMO
  horário (sobreposição), evento de 15 minutos;
- ocorrências de recorrentes com override (concluída, pulada, reagendada).

Uso:
    python manage.py seed_demo            # adiciona os dados demo
    python manage.py seed_demo --clear    # zera tarefas/eventos antes (mantém classes)

Idempotência: sem --clear o comando ACUMULA. Use --clear para um estado limpo.
"""

from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from planner.models import (
    Classe,
    Evento,
    Ocorrencia,
    RegistroExecucao,
    RegraRecorrencia,
    Tarefa,
)

# Dias da semana no padrão do projeto (0=seg … 6=dom).
SEG, TER, QUA, QUI, SEX, SAB = 0, 1, 2, 3, 4, 5


class Command(BaseCommand):
    help = "Semeia tarefas, eventos e histórico de execução realistas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Apaga tarefas/eventos/ocorrências/registros antes (mantém as classes).",
        )

    def handle(self, *args, **options):
        classes = {c.nome: c for c in Classe.objects.all()}
        faltando = {"Aula", "Tarefas básicas", "Estudar", "Prova", "Trabalho"} - set(
            classes
        )
        if faltando:
            raise CommandError(
                f"Classes padrão ausentes: {sorted(faltando)}. Rode as migrations."
            )

        with transaction.atomic():
            if options["clear"]:
                self._limpar()

            agora = timezone.localtime()
            hoje = agora.date()
            seg_semana = hoje - timedelta(days=hoje.weekday())  # segunda desta semana

            tarefas = self._criar_tarefas(classes, hoje)
            n_ev, n_oc = self._criar_eventos(classes, agora, hoje, seg_semana, tarefas)
            n_reg = self._criar_execucoes(classes, agora, hoje)

        self._resumo(tarefas, n_ev, n_oc, n_reg)

    # ------------------------------------------------------------------ #
    # Helpers de data                                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _at(dia, hora, minuto=0):
        """Datetime tz-aware no fuso ativo, em `dia` às `hora:minuto`."""
        return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))

    # ------------------------------------------------------------------ #
    # Limpeza                                                            #
    # ------------------------------------------------------------------ #
    def _limpar(self):
        n_reg = RegistroExecucao.objects.count()
        n_oc = Ocorrencia.objects.count()
        n_ev = Evento.objects.count()
        n_tar = Tarefa.objects.count()
        # Ocorrências caem por CASCADE ao apagar eventos; regras ficam órfãs.
        RegistroExecucao.objects.all().delete()
        Evento.objects.all().delete()
        RegraRecorrencia.objects.all().delete()
        Tarefa.objects.all().delete()
        self.stdout.write(
            self.style.WARNING(
                f"--clear: removidos {n_tar} tarefas, {n_ev} eventos, "
                f"{n_oc} ocorrências, {n_reg} registros de execução."
            )
        )

    # ------------------------------------------------------------------ #
    # Tarefas (Inbox)                                                    #
    # ------------------------------------------------------------------ #
    def _criar_tarefas(self, c, hoje):
        """Inbox cheio: elegíveis com prazos/esforços variados + edge cases."""
        d = lambda dias, h=18: self._at(hoje + timedelta(days=dias), h)  # noqa: E731
        # Próximo domingo (edge: deadline em fim de semana).
        prox_domingo = 6 - hoje.weekday() or 7

        specs = [
            # --- Elegíveis (deadline + esforço + classe): planejáveis ---
            ("Lista 4 de Cálculo II", c["Estudar"], d(2), 120, Tarefa.Status.INBOX),
            (
                "Estudar para a P2 de Física II",
                c["Prova"],
                d(5),
                300,
                Tarefa.Status.INBOX,
            ),
            (
                "Relatório do Lab de Física",
                c["Trabalho"],
                d(7),
                240,
                Tarefa.Status.INBOX,
            ),
            (
                "Implementar árvore AVL (AED)",
                c["Estudar"],
                d(6),
                180,
                Tarefa.Status.INBOX,
            ),
            ("Trabalho final de AED", c["Trabalho"], d(12), 480, Tarefa.Status.INBOX),
            (
                "Resumo do cap. 5 de Termodinâmica",
                c["Estudar"],
                d(9),
                90,
                Tarefa.Status.INBOX,
            ),
            (
                "Preparar apresentação do estágio",
                c["Trabalho"],
                d(10),
                150,
                Tarefa.Status.INBOX,
            ),
            (
                "Exercícios de Probabilidade",
                c["Estudar"],
                d(4, 12),
                90,
                Tarefa.Status.INBOX,
            ),
            (
                "Revisar anotações da semana",
                c["Estudar"],
                d(prox_domingo),
                60,
                Tarefa.Status.INBOX,
            ),
            (
                "Redação do writing (inglês)",
                c["Estudar"],
                d(prox_domingo + 6),
                45,
                Tarefa.Status.INBOX,
            ),
            (
                "Inscrição na iniciação científica",
                c["Tarefas básicas"],
                d(15),
                30,
                Tarefa.Status.INBOX,
            ),
            (
                "Projeto da disciplina optativa",
                c["Trabalho"],
                d(60),
                600,
                Tarefa.Status.INBOX,
            ),
            # --- Edges de prazo/esforço ---
            (
                "Responder e-mail do orientador",
                c["Tarefas básicas"],
                d(0, 21),
                15,
                Tarefa.Status.INBOX,
            ),
            (
                "Separar dúvidas para a monitoria",
                c["Estudar"],
                d(1, 9),
                30,
                Tarefa.Status.INBOX,
            ),
            (
                "Estudar TODO o semestre de Cálculo",
                c["Estudar"],
                d(3),
                720,
                Tarefa.Status.INBOX,
            ),
            (
                "Entregar formulário da bolsa",
                c["Tarefas básicas"],
                d(-1),
                30,
                Tarefa.Status.INBOX,
            ),
            (
                "Ler os capítulos 7, 8 e 9 de Sistemas Operacionais anotando "
                "as diferenças entre escalonadores preemptivos e cooperativos",
                c["Estudar"],
                d(8),
                200,
                Tarefa.Status.INBOX,
            ),
            (
                "Organizar férias 🏖 (pesquisar passagens)",
                c["Tarefas básicas"],
                d(30),
                60,
                Tarefa.Status.INBOX,
            ),
            # --- Inelegíveis para o planejador (faltam campos) ---
            ("Ler capítulo 4 de POO", c["Estudar"], None, 90, Tarefa.Status.INBOX),
            (
                "Organizar bibliografia",
                c["Tarefas básicas"],
                d(6),
                None,
                Tarefa.Status.INBOX,
            ),
            ("Comprar material de laboratório", None, None, None, Tarefa.Status.INBOX),
            # --- Já promovidas (têm eventos-sessão vinculados) ---
            (
                "Estudar P1 de Cálculo II",
                c["Estudar"],
                d(-2),
                180,
                Tarefa.Status.PROMOVIDA,
            ),
            (
                "Slides do seminário de Metodologia",
                c["Trabalho"],
                d(4),
                120,
                Tarefa.Status.PROMOVIDA,
            ),
        ]

        criadas = {}
        for titulo, classe, deadline, esforco, status in specs:
            criadas[titulo] = Tarefa.objects.create(
                titulo=titulo,
                descricao="",
                classe=classe,
                deadline=deadline,
                esforco_estimado=esforco,
                status=status,
            )
        return criadas

    # ------------------------------------------------------------------ #
    # Eventos + recorrência + ocorrências                                #
    # ------------------------------------------------------------------ #
    def _criar_eventos(self, c, agora, hoje, seg_semana, tarefas):
        at = self._at
        n_ev = 0

        def evento(
            titulo,
            classe,
            inicio,
            fim,
            *,
            rastrear,
            status=None,
            origem=None,
            regra=None,
        ):
            nonlocal n_ev
            n_ev += 1
            return Evento.objects.create(
                titulo=titulo,
                descricao="",
                inicio=inicio,
                fim=fim,
                classe=classe,
                rastrear_conclusao=rastrear,
                status=status if rastrear else None,
                origem_tarefa=origem,
                regra_recorrencia=regra,
            )

        def recorrente(titulo, classe, dia_base, h_ini, h_fim, dias, *, feriados=True):
            regra = RegraRecorrencia.objects.create(
                tipo=RegraRecorrencia.Tipo.SEMANAL,
                dias=dias,
                ignorar_feriados=feriados,
            )
            ini = (
                at(dia_base, *h_ini)
                if isinstance(h_ini, tuple)
                else at(dia_base, h_ini)
            )
            fim = (
                at(dia_base, *h_fim)
                if isinstance(h_fim, tuple)
                else at(dia_base, h_fim)
            )
            return evento(titulo, classe, ini, fim, rastrear=False, regra=regra)

        # --- Rotina recorrente (semestre): começa 4 semanas atrás para ter
        #     histórico, e serve de "ocupado" para o planejador. ---
        base = seg_semana - timedelta(days=28)

        calc = recorrente("Cálculo II", c["Aula"], base, 8, 10, [SEG, QUA])
        recorrente("Física II", c["Aula"], base + timedelta(days=1), 10, 12, [TER, QUI])
        recorrente(
            "Algoritmos e Estruturas de Dados", c["Aula"], base, 10, 12, [SEG, QUA]
        )
        recorrente("Lab de Física", c["Aula"], base + timedelta(days=4), 14, 16, [SEX])
        recorrente(
            "Estágio (dev júnior)",
            c["Trabalho"],
            base + timedelta(days=1),
            14,
            18,
            [TER, QUI],
            feriados=False,
        )
        recorrente(
            "Academia",
            c["Tarefas básicas"],
            base,
            19,
            (20, 30),
            [SEG, QUA, SEX],
            feriados=False,
        )
        recorrente("Inglês", c["Aula"], base + timedelta(days=5), 9, 11, [SAB])

        # Reunião mensal do grupo de pesquisa (dia 1, 14h).
        primeiro_mes_passado = (seg_semana.replace(day=1) - timedelta(days=1)).replace(
            day=1
        )
        r_reuniao = RegraRecorrencia.objects.create(
            tipo=RegraRecorrencia.Tipo.MENSAL, dias=[1], ignorar_feriados=False
        )
        evento(
            "Reunião do grupo de pesquisa",
            c["Trabalho"],
            at(primeiro_mes_passado, 14),
            at(primeiro_mes_passado, 15),
            rastrear=False,
            regra=r_reuniao,
        )

        # --- Histórico simples (últimas ~3 semanas, vários status) ---
        historico = [
            (
                "Estudar grafos (AED)",
                c["Estudar"],
                -18,
                14,
                16,
                Evento.Status.CONCLUIDO,
            ),
            (
                "Revisar listas antigas de Física",
                c["Estudar"],
                -14,
                15,
                17,
                Evento.Status.CONCLUIDO,
            ),
            (
                "Estudar integrais duplas",
                c["Estudar"],
                -10,
                19,
                21,
                Evento.Status.CONCLUIDO,
            ),
            (
                "Relatório parcial do estágio",
                c["Trabalho"],
                -7,
                9,
                11,
                Evento.Status.CONCLUIDO,
            ),
            (
                "Estudar Física (ondas)",
                c["Estudar"],
                -3,
                14,
                16,
                Evento.Status.CONCLUIDO,
            ),
            # Passado AGENDADO → deriva PENDENTE (status_efetivo).
            ("Revisar Cálculo", c["Estudar"], -1, 15, (16, 30), Evento.Status.AGENDADO),
            ("Lavar roupa", c["Tarefas básicas"], -2, 10, 11, Evento.Status.AGENDADO),
        ]
        for titulo, classe, off, h_ini, h_fim, status in historico:
            ini = at(
                hoje + timedelta(days=off),
                *(h_ini if isinstance(h_ini, tuple) else (h_ini,)),
            )
            fim = at(
                hoje + timedelta(days=off),
                *(h_fim if isinstance(h_fim, tuple) else (h_fim,)),
            )
            evento(titulo, classe, ini, fim, rastrear=True, status=status)

        # --- Hoje e futuro próximo ---
        evento(
            "Almoço com orientador",
            c["Tarefas básicas"],
            at(hoje, 12),
            at(hoje, 13),
            rastrear=False,
        )
        evento(
            "Consulta médica",
            c["Tarefas básicas"],
            at(hoje + timedelta(days=1), 9),
            at(hoje + timedelta(days=1), 10),
            rastrear=True,
            status=Evento.Status.REMARCADO,
        )
        evento(
            "Prova de Física II (P2)",
            c["Prova"],
            at(hoje + timedelta(days=5), 10),
            at(hoje + timedelta(days=5), 12),
            rastrear=False,
        )
        evento(
            "Prova de Cálculo II (P2)",
            c["Prova"],
            at(hoje + timedelta(days=13), 8),
            at(hoje + timedelta(days=13), 10),
            rastrear=False,
        )
        evento(
            "Entrega do trabalho final de AED",
            c["Trabalho"],
            at(hoje + timedelta(days=12), 23),
            at(hoje + timedelta(days=12), 23, 59),
            rastrear=True,
            status=Evento.Status.AGENDADO,
        )

        # --- Edges de UI/solver ---
        # Evento-dia-inteiro num sábado futuro.
        sab_futuro = hoje + timedelta(days=(SAB - hoje.weekday()) % 7 or 7)
        evento(
            "Maratona de programação",
            c["Estudar"],
            at(sab_futuro, 9),
            at(sab_futuro, 18),
            rastrear=False,
        )
        # Evento atravessando a meia-noite.
        evento(
            "Observação astronômica (extensão)",
            c["Aula"],
            at(hoje + timedelta(days=8), 22),
            at(hoje + timedelta(days=9), 1),
            rastrear=False,
        )
        # Dois eventos no MESMO horário (sobreposição deliberada).
        colisao = hoje + timedelta(days=3)
        evento(
            "Plantão de dúvidas de Cálculo",
            c["Aula"],
            at(colisao, 17),
            at(colisao, 18),
            rastrear=False,
        )
        evento(
            "Call semanal do estágio",
            c["Trabalho"],
            at(colisao, 17),
            at(colisao, 18),
            rastrear=False,
        )
        # Evento de 15 minutos.
        evento(
            "Daily do estágio",
            c["Trabalho"],
            at(hoje + timedelta(days=2), 9),
            at(hoje + timedelta(days=2), 9, 15),
            rastrear=False,
        )

        # --- Sessões das tarefas já PROMOVIDAS (origem_tarefa) ---
        p1 = tarefas["Estudar P1 de Cálculo II"]
        for offset in (-2, 1):
            evento(
                p1.titulo,
                p1.classe,
                at(hoje + timedelta(days=offset), 19),
                at(hoje + timedelta(days=offset), 20, 30),
                rastrear=True,
                status=(
                    Evento.Status.CONCLUIDO if offset < 0 else Evento.Status.AGENDADO
                ),
                origem=p1,
            )
        slides = tarefas["Slides do seminário de Metodologia"]
        evento(
            slides.titulo,
            slides.classe,
            at(hoje + timedelta(days=2), 16),
            at(hoje + timedelta(days=2), 18),
            rastrear=True,
            status=Evento.Status.AGENDADO,
            origem=slides,
        )

        # --- Ocorrências com override no "Cálculo II" (datas reais da série) ---
        n_oc = 0
        # Uma segunda passada → concluída.
        seg_passada = base + timedelta(days=7)
        Ocorrencia.objects.create(
            evento=calc, data=seg_passada, status_override=Evento.Status.CONCLUIDO
        )
        n_oc += 1
        # Próxima segunda → pulada (feriado/viagem).
        prox_seg = seg_semana + timedelta(days=7)
        Ocorrencia.objects.create(evento=calc, data=prox_seg, status_override="PULADO")
        n_oc += 1
        # Próxima quarta → remarcada para a tarde.
        prox_qua = seg_semana + timedelta(days=7 + QUA)
        Ocorrencia.objects.create(
            evento=calc,
            data=prox_qua,
            inicio_override=at(prox_qua, 14),
            fim_override=at(prox_qua, 16),
            status_override=Evento.Status.REMARCADO,
        )
        n_oc += 1

        return n_ev, n_oc

    # ------------------------------------------------------------------ #
    # Registros de execução (histórico do Marco C3)                       #
    # ------------------------------------------------------------------ #
    def _criar_execucoes(self, c, agora, hoje):
        """Histórico de ~4 semanas com viés proposital por classe, para os
        fatores adaptativos terem o que aprender:

        - Estudar: SUBESTIMA (real ≈ 1,3× o planejado) e remarca pouco;
        - Trabalho: estima bem (≈ 1,0);
        - Tarefas básicas: SUPERESTIMA (real ≈ 0,8×) e remarca bastante.
        """
        specs = [
            # (classe, planejado_min, real_min, remarcado, dias_atras)
            (c["Estudar"], 120, 160, False, 26),
            (c["Estudar"], 90, 115, False, 22),
            (c["Estudar"], 120, 150, False, 17),
            (c["Estudar"], 60, 80, False, 12),
            (c["Estudar"], 180, 235, False, 8),
            (c["Estudar"], 90, 120, False, 3),
            (c["Estudar"], 120, None, True, 15),  # remarcada (sem tempo real)
            (c["Trabalho"], 240, 235, False, 20),
            (c["Trabalho"], 120, 130, False, 13),
            (c["Trabalho"], 180, 175, False, 6),
            (c["Tarefas básicas"], 60, 45, False, 18),
            (c["Tarefas básicas"], 40, 30, False, 14),
            (c["Tarefas básicas"], 30, 25, False, 10),
            (c["Tarefas básicas"], 45, None, True, 9),  # remarcada
            (c["Tarefas básicas"], 30, None, True, 4),  # remarcada
        ]
        for classe, planejado, real, remarcado, dias in specs:
            RegistroExecucao.objects.create(
                classe=classe,
                planejado_min=planejado,
                real_min=real,
                remarcado=remarcado,
                concluido_em=None if remarcado else agora - timedelta(days=dias),
            )
        return len(specs)

    # ------------------------------------------------------------------ #
    # Resumo                                                             #
    # ------------------------------------------------------------------ #
    def _resumo(self, tarefas, n_ev, n_oc, n_reg):
        elegiveis = sum(
            1
            for t in tarefas.values()
            if t.status == Tarefa.Status.INBOX
            and t.deadline
            and t.esforco_estimado
            and t.classe_id
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seed concluído: {len(tarefas)} tarefas "
                f"({elegiveis} elegíveis ao planejador), {n_ev} eventos, "
                f"{n_oc} ocorrências com override, {n_reg} registros de execução."
            )
        )
