"""Marca `estrategia` nas tarefas que já existem (Fase 1.1, PR A).

Existe por causa de uma decisão do gate: **não há default de estratégia herdado
de classe** (D2 em `docs/tasks/fase1-parametros-por-tarefa.md`). Vale só o que
estiver explícito na tarefa. Como o frontend não mostra o campo e a IA que o
preencheria só chega no PR C, as tarefas criadas antes deste PR ficariam todas
sem estratégia — e o bug que motivou a task (estudo de prova agendado meses
antes) seguiria vivo no calendário real.

Este comando é a ponte: marca uma estratégia **explícita, tarefa por tarefa**,
em quem casar com um filtro. Não é migration nem default — é edição de dados
feita de propósito, e por isso:

- **sem `--aplicar` ele só LISTA** o que mudaria e sai sem tocar em nada;
- filtra por classe e/ou por texto no título, nunca "todas as tarefas";
- pula quem já tem estratégia definida, salvo `--sobrescrever`.

Uso:
    # ver o que mudaria (não grava nada)
    python manage.py marcar_estrategia --classe Estudar --estrategia TARDE

    # gravar
    python manage.py marcar_estrategia --classe Estudar --estrategia TARDE --aplicar

    # restringir por título
    python manage.py marcar_estrategia --titulo-contem "Estudar para a" \
        --estrategia TARDE --aplicar
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import Classe, Perfil, Tarefa


class Command(BaseCommand):
    help = "Marca estrategia (CEDO/TARDE) em tarefas existentes. Lista sem --aplicar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--estrategia",
            required=True,
            choices=[Tarefa.Estrategia.CEDO, Tarefa.Estrategia.TARDE],
        )
        parser.add_argument("--classe", help="Nome exato da classe (ex.: Estudar)")
        parser.add_argument("--titulo-contem", help="Filtra por trecho do título")
        parser.add_argument(
            "--perfil", help="ID do perfil; omitido ⇒ todos os perfis do banco"
        )
        parser.add_argument(
            "--sobrescrever",
            action="store_true",
            help="Inclui tarefas que já têm estratégia definida",
        )
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava. Sem esta flag o comando só lista.",
        )

    def handle(self, *args, **opts):
        classe_nome = opts.get("classe")
        titulo = opts.get("titulo_contem")
        if not classe_nome and not titulo:
            raise CommandError(
                "Informe --classe e/ou --titulo-contem. Marcar TODAS as tarefas "
                "de uma vez seria um default disfarçado — e o gate recusou default."
            )

        perfis = Perfil.objects.all()
        if opts.get("perfil"):
            perfis = perfis.filter(id=opts["perfil"])
        if not perfis.exists():
            raise CommandError("Nenhum perfil encontrado com esse filtro.")

        estrategia = opts["estrategia"]
        total = 0
        for perfil in perfis:
            qs = Tarefa.objects.do_dono(perfil)
            if classe_nome:
                classe = Classe.objects.do_dono(perfil).filter(nome=classe_nome).first()
                if classe is None:
                    self.stdout.write(
                        f"perfil {perfil.id}: sem classe {classe_nome!r}, pulando"
                    )
                    continue
                qs = qs.filter(classe=classe)
            if titulo:
                qs = qs.filter(titulo__icontains=titulo)
            if not opts["sobrescrever"]:
                qs = qs.filter(estrategia__isnull=True)

            alvos = list(qs.order_by("deadline", "titulo"))
            if not alvos:
                continue
            self.stdout.write(f"\nperfil {perfil.id} — {len(alvos)} tarefa(s):")
            for t in alvos:
                atual = t.estrategia or "—"
                self.stdout.write(f"  {atual:>5} → {estrategia}  {t.titulo}")
            total += len(alvos)

            if opts["aplicar"]:
                with transaction.atomic():
                    for t in alvos:
                        t.estrategia = estrategia
                        t.save(update_fields=["estrategia", "atualizado_em"])

        if not total:
            self.stdout.write("Nada a fazer: nenhuma tarefa casou com o filtro.")
            return
        if opts["aplicar"]:
            self.stdout.write(self.style.SUCCESS(f"\n{total} tarefa(s) marcada(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"\nSimulação: {total} tarefa(s) seriam marcadas. "
                    "Repita com --aplicar para gravar."
                )
            )
