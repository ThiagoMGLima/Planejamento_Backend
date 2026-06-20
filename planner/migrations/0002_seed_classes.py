"""Semeia as 5 classes padrão (Handoff §4.1).

No handoff, o seed acontecia no signal post_save de User. Como o projeto é
local/single-user (sem usuários — ver PLAN.md), o seed roda aqui, no migrate.
Cores e flag de rastreamento vêm da §4.1.
"""

from django.db import migrations

CLASSES_PADRAO = [
    # (nome, cor, rastreia_conclusao)
    ("Aula", "#e6f1fb", False),
    ("Tarefas básicas", "#f0efe9", False),
    ("Estudar", "#ecf4df", True),
    ("Prova", "#fbeaea", False),
    ("Trabalho", "#e1f5ee", True),
]


def criar_classes_padrao(apps, schema_editor):
    Classe = apps.get_model("planner", "Classe")
    for nome, cor, rastreia in CLASSES_PADRAO:
        Classe.objects.update_or_create(
            nome=nome,
            defaults={"cor": cor, "rastreia_conclusao": rastreia},
        )


def remover_classes_padrao(apps, schema_editor):
    Classe = apps.get_model("planner", "Classe")
    nomes = [nome for nome, _, _ in CLASSES_PADRAO]
    # Só remove se não houver eventos protegendo a classe (PROTECT).
    for classe in Classe.objects.filter(nome__in=nomes):
        if not classe.eventos.exists():
            classe.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("planner", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(criar_classes_padrao, remover_classes_padrao),
    ]
