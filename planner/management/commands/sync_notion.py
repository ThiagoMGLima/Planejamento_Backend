"""Puxa tarefas novas do Notion para a Inbox (mesmo service do endpoint).

Útil para rodar/depurar a sincronização sem a UI. O fluxo do dia a dia é pelo
botão "Sincronizar com Notion" no app web; este comando é o mesmo motor.

Uso:
    python manage.py sync_notion
"""

from django.core.management.base import BaseCommand, CommandError

from planner.services import notion_sync


class Command(BaseCommand):
    help = "Importa tarefas novas da database do Notion para a Inbox."

    def handle(self, *args, **options):
        try:
            resumo = notion_sync.sincronizar()
        except notion_sync.NotionDesligado as e:
            raise CommandError(f"Integração desligada: {e}")
        except notion_sync.NotionIndisponivel as e:
            raise CommandError(f"Notion indisponível: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync concluído: {resumo['importadas']} importadas, "
                f"{resumo['ignoradas']} já existentes, {len(resumo['erros'])} com erro."
            )
        )
        for erro in resumo["erros"]:
            self.stdout.write(
                self.style.WARNING(f"  - {erro['page_id']}: {erro['motivo']}")
            )
