"""Semeia um dataset de demonstração balanceado (tarefas + eventos).

As 5 classes padrão já vêm da migration 0002; aqui criamos dados de exemplo
ancorados em "hoje" para exercitar todas as telas e, em especial, o planejador
de produção multitarefa: tarefas elegíveis e inválidas, deadlines variadas,
eventos simples (passado/presente/futuro), recorrentes (semanais/mensal) que
servem de "ocupado" para o cálculo, e ocorrências com overrides.

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
    RegraRecorrencia,
    Tarefa,
)

# Dias da semana no padrão do projeto (0=seg … 6=dom).
SEG, TER, QUA, QUI, SEX = 0, 1, 2, 3, 4


class Command(BaseCommand):
    help = "Semeia tarefas e eventos de demonstração (balanceado)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Apaga tarefas/eventos/ocorrências antes (mantém as classes).",
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

        self._resumo(tarefas, n_ev, n_oc)

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
        n_oc = Ocorrencia.objects.count()
        n_ev = Evento.objects.count()
        n_tar = Tarefa.objects.count()
        # Ocorrências caem por CASCADE ao apagar eventos; regras ficam órfãs.
        Evento.objects.all().delete()
        RegraRecorrencia.objects.all().delete()
        Tarefa.objects.all().delete()
        self.stdout.write(
            self.style.WARNING(
                f"--clear: removidos {n_tar} tarefas, {n_ev} eventos, {n_oc} ocorrências."
            )
        )

    # ------------------------------------------------------------------ #
    # Tarefas (Inbox)                                                    #
    # ------------------------------------------------------------------ #
    def _criar_tarefas(self, c, hoje):
        """Mix de tarefas: elegíveis ao planejador, inválidas e já promovida."""
        d = lambda dias, h=18: self._at(hoje + timedelta(days=dias), h)  # noqa: E731

        specs = [
            # --- Elegíveis (deadline + esforço + classe): planejáveis ---
            ("Estudar P2 de Cálculo", c["Estudar"], d(5), 300, Tarefa.Status.INBOX),
            ("Lista de Álgebra Linear", c["Estudar"], d(2), 120, Tarefa.Status.INBOX),
            (
                "Revisar para prova de Química",
                c["Prova"],
                d(3),
                180,
                Tarefa.Status.INBOX,
            ),
            (
                "Relatório de Física Experimental",
                c["Trabalho"],
                d(7),
                240,
                Tarefa.Status.INBOX,
            ),
            ("Trabalho final de ASL", c["Trabalho"], d(11), 360, Tarefa.Status.INBOX),
            (
                "Preparar seminário de História",
                c["Estudar"],
                d(14),
                200,
                Tarefa.Status.INBOX,
            ),
            (
                "Exercícios de Estatística",
                c["Estudar"],
                d(4, 12),
                90,
                Tarefa.Status.INBOX,
            ),
            # --- Borda: deadline no passado (vira nao_alocado se selecionada) ---
            (
                "Entregar formulário da bolsa",
                c["Tarefas básicas"],
                d(-1),
                30,
                Tarefa.Status.INBOX,
            ),
            # --- Inválidas para o planejador (faltam campos) ---
            (
                "Ler capítulo 4 de POO",
                c["Estudar"],
                None,
                90,
                Tarefa.Status.INBOX,
            ),  # sem deadline
            (
                "Organizar bibliografia",
                c["Tarefas básicas"],
                d(6),
                None,
                Tarefa.Status.INBOX,
            ),  # sem esforço
            (
                "Comprar material de laboratório",
                None,
                None,
                None,
                Tarefa.Status.INBOX,
            ),  # sem nada
            # --- Já promovida (tem eventos-sessão vinculados) ---
            (
                "Estudar P1 de Cálculo",
                c["Estudar"],
                d(-2),
                180,
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

        # --- Recorrentes (rotina fixa): começam 2 semanas atrás para terem
        #     histórico, e servem de "ocupado" para o planejador. ---
        base = seg_semana - timedelta(days=14)

        r_calc = RegraRecorrencia.objects.create(
            tipo=RegraRecorrencia.Tipo.SEMANAL, dias=[SEG, QUA], ignorar_feriados=True
        )
        calc = evento(
            "Cálculo I",
            c["Aula"],
            at(base, 8),
            at(base, 10),
            rastrear=False,
            regra=r_calc,
        )

        r_fis = RegraRecorrencia.objects.create(
            tipo=RegraRecorrencia.Tipo.SEMANAL, dias=[TER, QUI], ignorar_feriados=True
        )
        evento(
            "Física I",
            c["Aula"],
            at(base + timedelta(days=1), 10),
            at(base + timedelta(days=1), 12),
            rastrear=False,
            regra=r_fis,
        )

        # Academia à noite (seg/qua/sex) — colide com o horário "preferido" de
        # estudo, ótimo para exercitar o anti-conflito do planejador.
        r_acad = RegraRecorrencia.objects.create(
            tipo=RegraRecorrencia.Tipo.SEMANAL,
            dias=[SEG, QUA, SEX],
            ignorar_feriados=False,
        )
        evento(
            "Academia",
            c["Tarefas básicas"],
            at(base, 19),
            at(base, 20, 30),
            rastrear=False,
            regra=r_acad,
        )

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

        # --- Eventos simples variados (passado/presente/futuro, vários status) ---
        # Passado concluído.
        evento(
            "Estudar Física",
            c["Estudar"],
            at(hoje - timedelta(days=3), 14),
            at(hoje - timedelta(days=3), 16),
            rastrear=True,
            status=Evento.Status.CONCLUIDO,
        )
        # Passado AGENDADO → deriva PENDENTE (status_efetivo).
        evento(
            "Revisar Cálculo",
            c["Estudar"],
            at(hoje - timedelta(days=1), 15),
            at(hoje - timedelta(days=1), 16, 30),
            rastrear=True,
            status=Evento.Status.AGENDADO,
        )
        # Hoje.
        evento(
            "Almoço com orientador",
            c["Tarefas básicas"],
            at(hoje, 12),
            at(hoje, 13),
            rastrear=False,
        )
        # Remarcado.
        evento(
            "Consulta médica",
            c["Tarefas básicas"],
            at(hoje + timedelta(days=1), 9),
            at(hoje + timedelta(days=1), 10),
            rastrear=True,
            status=Evento.Status.REMARCADO,
        )
        # Prova futura.
        evento(
            "Prova de Química",
            c["Prova"],
            at(hoje + timedelta(days=3), 10),
            at(hoje + timedelta(days=3), 12),
            rastrear=False,
        )
        # Entrega futura.
        evento(
            "Entrega do Trabalho de ASL",
            c["Trabalho"],
            at(hoje + timedelta(days=11), 23),
            at(hoje + timedelta(days=11), 23, 59),
            rastrear=True,
            status=Evento.Status.AGENDADO,
        )

        # --- Sessões da tarefa já PROMOVIDA (origem_tarefa) ---
        promovida = tarefas["Estudar P1 de Cálculo"]
        for offset in (-2, 1):
            evento(
                promovida.titulo,
                promovida.classe,
                at(hoje + timedelta(days=offset), 19),
                at(hoje + timedelta(days=offset), 20, 30),
                rastrear=True,
                status=(
                    Evento.Status.CONCLUIDO if offset < 0 else Evento.Status.AGENDADO
                ),
                origem=promovida,
            )

        # --- Ocorrências com override no "Cálculo I" (datas reais da série) ---
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
    # Resumo                                                             #
    # ------------------------------------------------------------------ #
    def _resumo(self, tarefas, n_ev, n_oc):
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
                f"{n_oc} ocorrências com override."
            )
        )
