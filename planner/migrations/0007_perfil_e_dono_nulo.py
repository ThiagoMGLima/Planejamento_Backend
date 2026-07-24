"""Fase 0B / PR1, parte 1 de 3: cria o `Perfil` e abre a coluna `dono`.

O `dono` nasce NULO **de propósito**: é a única forma de adicionar a coluna a
tabelas que já têm dados. A 0008 preenche e a 0009 aperta para NOT NULL — as
três formam uma unidade, e rodar só esta deixa o schema num estado intermediário
que o código não suporta.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def dono_nulo(related_name):
    return models.ForeignKey(
        null=True,  # temporário — apertado na 0009
        on_delete=django.db.models.deletion.CASCADE,
        related_name=related_name,
        to="planner.perfil",
    )


class Migration(migrations.Migration):

    dependencies = [("planner", "0006_seed_feriados_curitiba")]

    operations = [
        migrations.CreateModel(
            name="Perfil",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("supabase_id", models.UUIDField(blank=True, null=True, unique=True)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("nome", models.CharField(blank=True, max_length=120)),
                (
                    "plano",
                    models.CharField(
                        choices=[
                            ("DEMO", "Demo"),
                            ("TRIAL", "Trial"),
                            ("PRO", "Pro"),
                        ],
                        default="TRIAL",
                        max_length=8,
                    ),
                ),
                ("trial_ate", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Perfil",
                "verbose_name_plural": "Perfis",
                "ordering": ["email"],
            },
        ),
        migrations.AddField(
            model_name="classe", name="dono", field=dono_nulo("classes")
        ),
        migrations.AddField(
            model_name="tarefa", name="dono", field=dono_nulo("tarefas")
        ),
        migrations.AddField(
            model_name="evento", name="dono", field=dono_nulo("eventos")
        ),
        migrations.AddField(
            model_name="regrarecorrencia", name="dono", field=dono_nulo("regras")
        ),
        migrations.AddField(
            model_name="pesopreferencia", name="dono", field=dono_nulo("pesos")
        ),
        migrations.AddField(
            model_name="escolhacenario", name="dono", field=dono_nulo("escolhas")
        ),
        migrations.AddField(
            model_name="registroexecucao", name="dono", field=dono_nulo("execucoes")
        ),
        migrations.AddField(
            model_name="feriadolocal", name="dono", field=dono_nulo("feriados")
        ),
    ]
