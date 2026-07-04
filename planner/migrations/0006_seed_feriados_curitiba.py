"""Semeia o feriado municipal de Curitiba (Marco C8 — Curitiba primeiro).

O Paraná não tem feriado estadual oficial (o 19/12, emancipação, é data
comemorativa), então a camada regional de Curitiba se resume ao municipal:
08/09 — Nossa Senhora da Luz dos Pinhais (padroeira da cidade, Lei municipal).
Outros feriados locais podem ser adicionados no admin (Feriados locais).
"""

from django.db import migrations

FERIADOS_CURITIBA = [
    # (nome, dia, mes) — ano nulo: recorre todo ano
    ("Nossa Senhora da Luz dos Pinhais (padroeira de Curitiba)", 8, 9),
]


def criar_feriados_curitiba(apps, schema_editor):
    FeriadoLocal = apps.get_model("planner", "FeriadoLocal")
    for nome, dia, mes in FERIADOS_CURITIBA:
        FeriadoLocal.objects.get_or_create(
            dia=dia, mes=mes, ano=None, defaults={"nome": nome}
        )


def remover_feriados_curitiba(apps, schema_editor):
    FeriadoLocal = apps.get_model("planner", "FeriadoLocal")
    for _nome, dia, mes in FERIADOS_CURITIBA:
        FeriadoLocal.objects.filter(dia=dia, mes=mes, ano=None).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("planner", "0005_feriadolocal"),
    ]

    operations = [
        migrations.RunPython(criar_feriados_curitiba, remover_feriados_curitiba),
    ]
