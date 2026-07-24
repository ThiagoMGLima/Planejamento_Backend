"""Fase 0B / PR1, parte 2 de 3: cria o perfil local e adota os dados existentes.

Todo dado que existe hoje foi criado por um app single-user, então tem um dono
óbvio: a pessoa que rodou o app. Esta migration materializa esse dono e atribui
tudo a ele — inclusive as 5 classes padrão da 0002 e o feriado de Curitiba da
0006, que nasceram globais.

O perfil local tem **PK fixa** (`UUID_PERFIL_LOCAL`) em vez de ser procurado por
e-mail: é assim que o código (`services/perfis.py`) o reencontra sem depender de
um texto que alguém pode querer editar depois. No PR2, o primeiro login grava o
`supabase_id` **neste** perfil — a conta local vira a conta autenticada e os
dados de dev seguem no lugar (decisão Q4 do plano).
"""

from django.db import migrations

# Mantido em sincronia com services/perfis.UUID_PERFIL_LOCAL. É um sentinel:
# fixo de propósito, para que código e histórico apontem para a mesma linha.
UUID_PERFIL_LOCAL = "00000000-0000-0000-0000-000000000001"

MODELS_COM_DONO = [
    "Classe",
    "Tarefa",
    "Evento",
    "RegraRecorrencia",
    "PesoPreferencia",
    "EscolhaCenario",
    "RegistroExecucao",
    "FeriadoLocal",
]


def adotar_dados_existentes(apps, schema_editor):
    Perfil = apps.get_model("planner", "Perfil")
    perfil, _ = Perfil.objects.get_or_create(
        id=UUID_PERFIL_LOCAL,
        defaults={
            "email": "local@planejador.local",
            "nome": "Perfil local",
            # Sem gate de pagamento ainda (PR3); o perfil local é o dono de tudo
            # e não deve esbarrar em limite nenhum enquanto isso.
            "plano": "PRO",
        },
    )
    for nome in MODELS_COM_DONO:
        apps.get_model("planner", nome).objects.filter(dono__isnull=True).update(
            dono=perfil
        )


class Migration(migrations.Migration):

    dependencies = [("planner", "0007_perfil_e_dono_nulo")]

    operations = [
        # Sem reverso: desfazer a 0009 devolve `dono` a nulo, e deixar os dados
        # apontando para o perfil local é inofensivo (nada mais os lê como
        # globais). Apagar o perfil aqui é que seria destrutivo.
        migrations.RunPython(adotar_dados_existentes, migrations.RunPython.noop),
    ]
