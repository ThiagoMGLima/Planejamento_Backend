"""Fase 0B / PR1, parte 3 de 3: `dono` vira NOT NULL e a unicidade vira por-dono.

Com os dados já adotados pela 0008, o banco passa a garantir o que o código
assume: **não existe linha sem dono**. As três constraints globais que
impediriam um segundo usuário de existir são trocadas pelas suas versões
por-dono aqui, e não antes, porque só agora `dono` está preenchido.
"""

import django.db.models.deletion
from django.db import migrations, models


def dono(related_name):
    return models.ForeignKey(
        on_delete=django.db.models.deletion.CASCADE,
        related_name=related_name,
        to="planner.perfil",
    )


class Migration(migrations.Migration):

    dependencies = [("planner", "0008_backfill_perfil_local")]

    operations = [
        # 1) `dono` obrigatório nos 8 models-raiz.
        migrations.AlterField(model_name="classe", name="dono", field=dono("classes")),
        migrations.AlterField(model_name="tarefa", name="dono", field=dono("tarefas")),
        migrations.AlterField(model_name="evento", name="dono", field=dono("eventos")),
        migrations.AlterField(
            model_name="regrarecorrencia", name="dono", field=dono("regras")
        ),
        migrations.AlterField(
            model_name="pesopreferencia", name="dono", field=dono("pesos")
        ),
        migrations.AlterField(
            model_name="escolhacenario", name="dono", field=dono("escolhas")
        ),
        migrations.AlterField(
            model_name="registroexecucao", name="dono", field=dono("execucoes")
        ),
        migrations.AlterField(
            model_name="feriadolocal", name="dono", field=dono("feriados")
        ),
        # 2) Unicidade global → por-dono.
        #    `Classe.nome`: sem isto, o 2º usuário não consegue ter "Estudar".
        migrations.AlterField(
            model_name="classe", name="nome", field=models.CharField(max_length=80)
        ),
        migrations.AddConstraint(
            model_name="classe",
            constraint=models.UniqueConstraint(
                fields=("dono", "nome"), name="uq_classe_dono_nome"
            ),
        ),
        #    `PesoPreferencia.metrica`: era o pior dos três — o 1º perfil a
        #    gravar um peso travava o aprendizado de todos os outros.
        migrations.AlterField(
            model_name="pesopreferencia",
            name="metrica",
            field=models.CharField(max_length=40),
        ),
        migrations.AddConstraint(
            model_name="pesopreferencia",
            constraint=models.UniqueConstraint(
                fields=("dono", "metrica"), name="uq_peso_dono_metrica"
            ),
        ),
        #    `FeriadoLocal`: um feriado municipal por data no sistema inteiro.
        migrations.RemoveConstraint(
            model_name="feriadolocal", name="uq_feriadolocal_data"
        ),
        migrations.AddConstraint(
            model_name="feriadolocal",
            constraint=models.UniqueConstraint(
                fields=("dono", "dia", "mes", "ano"), name="uq_feriadolocal_dono_data"
            ),
        ),
        # 3) O índice da janela passa a começar pelo dono — é por ele que toda
        #    consulta de agenda filtra primeiro agora.
        migrations.RemoveIndex(
            model_name="evento", name="planner_eve_inicio_1e702b_idx"
        ),
        migrations.AddIndex(
            model_name="evento",
            index=models.Index(
                fields=["dono", "inicio", "fim"], name="ix_evento_dono_janela"
            ),
        ),
    ]
