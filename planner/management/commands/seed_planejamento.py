"""Semeia um dataset GRANDE e voltado para frente, focado no planejador.

Diferente do `seed_demo` (variado, com bastante histórico), este comando olha só
para as ~6 semanas a partir de HOJE e foi desenhado para exercitar as features
recentes do planejamento por IA:

- deadlines espalhadas de poucos dias até ~6 semanas → testa os horizontes
  SEMANA / DUAS_SEMANAS / MES (filtro de escopo) e a estimativa de tempo;
- semanas 2 e 3 DELIBERADAMENTE sobrecarregadas (vários trabalhos/provas grandes
  com prazos próximos) → dá material para a IA distribuir e suavizar picos
  (max_min_por_dia / max_min_por_dia_total);
- rotina recorrente (aulas, estágio, academia) como "ocupado" que disputa as
  janelas de estudo, forçando o solver a espalhar.

Uso:
    python manage.py seed_planejamento            # adiciona os dados
    python manage.py seed_planejamento --clear    # zera tarefas/eventos antes

Idempotência: sem --clear o comando ACUMULA. Use --clear para um estado limpo.
"""

from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from planner.models import (
    Classe,
    Evento,
    RegraRecorrencia,
    Tarefa,
)

# Dias da semana no padrão do projeto (0=seg … 6=dom).
SEG, TER, QUA, QUI, SEX = 0, 1, 2, 3, 4


class Command(BaseCommand):
    help = "Semeia um dataset grande, futuro, focado no planejador por IA."

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

            hoje = timezone.localtime().date()
            seg_semana = hoje - timedelta(days=hoje.weekday())  # segunda desta semana

            tarefas = self._criar_tarefas(classes, hoje)
            n_ev = self._criar_eventos(classes, hoje, seg_semana)

        self._resumo(tarefas, n_ev)

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
        n_ev = Evento.objects.count()
        n_tar = Tarefa.objects.count()
        # Ocorrências caem por CASCADE ao apagar eventos; regras ficam órfãs.
        Evento.objects.all().delete()
        RegraRecorrencia.objects.all().delete()
        Tarefa.objects.all().delete()
        self.stdout.write(
            self.style.WARNING(f"--clear: removidos {n_tar} tarefas e {n_ev} eventos.")
        )

    # ------------------------------------------------------------------ #
    # Tarefas (Inbox) — todas a partir de hoje, deadlines em ~6 semanas  #
    # ------------------------------------------------------------------ #
    def _criar_tarefas(self, c, hoje):
        """Carga densa de tarefas planejáveis + um punhado de inválidas."""
        d = lambda dias, h=18: self._at(hoje + timedelta(days=dias), h)  # noqa: E731

        # (titulo, classe, deadline, esforço_min). Os offsets agrupam por semana;
        # esforços grandes (≥300) nas semanas 2–3 criam os picos a suavizar.
        specs = [
            # --- Semana 1 (d1–d7): ritmo normal -------------------------------
            ("Lista 1 de Cálculo II", c["Estudar"], d(2, 12), 120),
            ("Resumo de Termodinâmica", c["Estudar"], d(4), 90),
            ("Exercícios de Álgebra Linear", c["Estudar"], d(5), 150),
            ("Prova de Cálculo II", c["Prova"], d(6, 10), 300),
            ("Relatório do Lab de Física", c["Trabalho"], d(7), 240),
            # --- Semana 2 (d8–d14): SOBRECARGA (provas + entregas juntas) -----
            ("Trabalho de Estruturas de Dados", c["Trabalho"], d(10), 420),
            ("Seminário de Sistemas Operacionais", c["Estudar"], d(11), 240),
            ("Lista de Redes de Computadores", c["Estudar"], d(12, 12), 120),
            ("Prova de Estatística", c["Prova"], d(12, 10), 360),
            ("Revisão de Química Orgânica", c["Prova"], d(13), 300),
            ("Entrega do artigo de IC", c["Trabalho"], d(14, 23), 480),
            # --- Semana 3 (d15–d21): ainda pesada -----------------------------
            ("Projeto de Banco de Dados", c["Trabalho"], d(17), 360),
            ("Estudar Cálculo Numérico", c["Estudar"], d(18), 180),
            ("Trabalho de Engenharia de Software", c["Trabalho"], d(19), 300),
            ("Lista de Probabilidade", c["Estudar"], d(20, 12), 120),
            # --- Semana 4 (d22–d28): provas finais começam --------------------
            ("Prova final de Física II", c["Prova"], d(24, 10), 420),
            ("Apresentação parcial do TCC", c["Trabalho"], d(26), 300),
            ("Resumo de Compiladores", c["Estudar"], d(27), 150),
            # --- Semanas 5–6 (d29–d40): além do horizonte de 1 mês ------------
            ("Trabalho final de IA", c["Trabalho"], d(32, 23), 480),
            ("Estudar para prova de Cálculo III", c["Estudar"], d(35, 10), 300),
            ("Seminário de História da Ciência", c["Estudar"], d(40), 240),
            # --- Inválidas para o planejador (faltam campos) ------------------
            ("Ler capítulo 4 de POO", c["Estudar"], None, 90),  # sem deadline
            (
                "Organizar bibliografia do TCC",
                c["Tarefas básicas"],
                d(9),
                None,
            ),  # s/ esf.
            ("Comprar material de laboratório", None, None, None),  # sem nada
        ]

        criadas = {}
        for titulo, classe, deadline, esforco in specs:
            criadas[titulo] = Tarefa.objects.create(
                titulo=titulo,
                descricao="",
                classe=classe,
                deadline=deadline,
                esforco_estimado=esforco,
                status=Tarefa.Status.INBOX,
            )
        return criadas

    # ------------------------------------------------------------------ #
    # Eventos: rotina recorrente (ocupado) + provas/entregas futuras     #
    # ------------------------------------------------------------------ #
    def _criar_eventos(self, c, hoje, seg_semana):
        at = self._at
        n_ev = 0

        def evento(
            titulo, classe, inicio, fim, *, rastrear=False, status=None, regra=None
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
                regra_recorrencia=regra,
            )

        def recorrente(titulo, classe, dias, h_ini, h_fim, *, ignorar_feriados=True):
            regra = RegraRecorrencia.objects.create(
                tipo=RegraRecorrencia.Tipo.SEMANAL,
                dias=dias,
                ignorar_feriados=ignorar_feriados,
            )
            # Âncora na segunda desta semana; a série se expande para frente.
            primeiro = seg_semana + timedelta(days=dias[0])
            evento(
                titulo,
                classe,
                at(primeiro, h_ini[0], h_ini[1]),
                at(primeiro, h_fim[0], h_fim[1]),
                regra=regra,
            )

        # --- Rotina fixa (ocupa as janelas de estudo) ---------------------
        recorrente("Cálculo II", c["Aula"], [SEG, QUA], (8, 0), (10, 0))
        recorrente("Física II", c["Aula"], [TER, QUI], (10, 0), (12, 0))
        recorrente("Estruturas de Dados", c["Aula"], [SEG, QUA], (14, 0), (16, 0))
        # Estágio: blocos grandes à tarde — derrubam a capacidade de ter/qui.
        recorrente("Estágio", c["Trabalho"], [TER, QUI], (14, 0), (18, 0))
        # Academia à noite colide com o horário "preferido" de estudo.
        recorrente(
            "Academia",
            c["Tarefas básicas"],
            [SEG, QUA, SEX],
            (19, 0),
            (20, 30),
            ignorar_feriados=False,
        )

        # Reunião mensal do grupo de pesquisa (dia 1, 14h).
        r_reuniao = RegraRecorrencia.objects.create(
            tipo=RegraRecorrencia.Tipo.MENSAL, dias=[1], ignorar_feriados=False
        )
        prox_dia1 = (seg_semana.replace(day=1) + timedelta(days=32)).replace(day=1)
        evento(
            "Reunião do grupo de pesquisa",
            c["Trabalho"],
            at(prox_dia1, 14),
            at(prox_dia1, 15),
            regra=r_reuniao,
        )

        # --- Marcos futuros pontuais (provas/entregas a evitar no plano) ---
        marcos = [
            ("Prova de Cálculo II", c["Prova"], 6, 10, 12),
            ("Prova de Estatística", c["Prova"], 12, 10, 12),
            ("Prova final de Física II", c["Prova"], 24, 10, 12),
        ]
        for titulo, classe, off, h_ini, h_fim in marcos:
            evento(
                titulo,
                classe,
                at(hoje + timedelta(days=off), h_ini),
                at(hoje + timedelta(days=off), h_fim),
            )

        # Entrega noturna (deadline "duro") da IC.
        evento(
            "Entrega do artigo de IC",
            c["Trabalho"],
            at(hoje + timedelta(days=14), 23),
            at(hoje + timedelta(days=14), 23, 59),
            rastrear=True,
            status=Evento.Status.AGENDADO,
        )

        return n_ev

    # ------------------------------------------------------------------ #
    # Resumo                                                             #
    # ------------------------------------------------------------------ #
    def _resumo(self, tarefas, n_ev):
        elegiveis = [
            t
            for t in tarefas.values()
            if t.deadline and t.esforco_estimado and t.classe_id
        ]
        esforco_total = sum(t.esforco_estimado for t in elegiveis)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seed concluído: {len(tarefas)} tarefas "
                f"({len(elegiveis)} elegíveis ao planejador, "
                f"{esforco_total} min de esforço somado em ~6 semanas), "
                f"{n_ev} eventos (rotina recorrente + marcos)."
            )
        )
