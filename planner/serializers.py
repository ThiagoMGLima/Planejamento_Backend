"""Serializers e validações (Handoff §9).

Marco 2: CRUD de Classe, Tarefa e Evento + promover. A derivação real de
`status_efetivo` (PENDENTE) chega no Marco 3; aqui é um stub que devolve o
`status` persistido.
"""

import re

from rest_framework import serializers

from .models import Classe, Evento, RegraRecorrencia, Tarefa

COR_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class ClasseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Classe
        fields = [
            "id",
            "nome",
            "cor",
            "rastreia_conclusao",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "criado_em", "atualizado_em"]

    def validate_cor(self, value):
        if not COR_HEX.match(value):
            raise serializers.ValidationError("Cor deve ser um hex no formato #RRGGBB.")
        return value


class TarefaSerializer(serializers.ModelSerializer):
    # Leitura aninhada da classe; escrita por id.
    classe = ClasseSerializer(read_only=True)
    classe_id = serializers.PrimaryKeyRelatedField(
        queryset=Classe.objects.all(),
        source="classe",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Tarefa
        fields = [
            "id",
            "titulo",
            "descricao",
            "classe",
            "classe_id",
            "deadline",
            "esforco_estimado",
            "status",
            "criado_em",
            "atualizado_em",
        ]
        # status é controlado pela máquina de estados (promover), não pelo cliente.
        read_only_fields = ["id", "status", "criado_em", "atualizado_em"]


class RegraRecorrenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = RegraRecorrencia
        fields = ["id", "tipo", "dias", "ignorar_feriados", "data_fim"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        tipo = attrs.get("tipo", getattr(self.instance, "tipo", None))
        dias = attrs.get("dias", getattr(self.instance, "dias", None))
        if dias is not None:
            if len(dias) == 0:
                raise serializers.ValidationError({"dias": "Informe ao menos um dia."})
            if tipo == RegraRecorrencia.Tipo.SEMANAL:
                if any(d < 0 or d > 6 for d in dias):
                    raise serializers.ValidationError(
                        {"dias": "Para SEMANAL, dias devem estar entre 0 e 6."}
                    )
            elif tipo == RegraRecorrencia.Tipo.MENSAL:
                if any(d < 1 or d > 31 for d in dias):
                    raise serializers.ValidationError(
                        {"dias": "Para MENSAL, dias devem estar entre 1 e 31."}
                    )
        return attrs


class EventoSerializer(serializers.ModelSerializer):
    classe = ClasseSerializer(read_only=True)
    classe_id = serializers.PrimaryKeyRelatedField(
        queryset=Classe.objects.all(), source="classe", write_only=True
    )
    regra_recorrencia = RegraRecorrenciaSerializer(required=False, allow_null=True)
    origem_tarefa = serializers.PrimaryKeyRelatedField(read_only=True)
    status_efetivo = serializers.SerializerMethodField()

    class Meta:
        model = Evento
        fields = [
            "id",
            "titulo",
            "descricao",
            "inicio",
            "fim",
            "classe",
            "classe_id",
            "rastrear_conclusao",
            "status",
            "status_efetivo",
            "origem_tarefa",
            "regra_recorrencia",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "origem_tarefa", "criado_em", "atualizado_em"]
        extra_kwargs = {
            # Herdado da classe quando ausente (ver validate()).
            "rastrear_conclusao": {"required": False},
            "status": {"required": False, "allow_null": True},
        }

    def get_status_efetivo(self, obj):
        """Derivação de PENDENTE via services/completion (Handoff §5.1)."""
        from .services.completion import status_efetivo

        return status_efetivo(obj)

    def validate(self, attrs):
        inicio = attrs.get("inicio", getattr(self.instance, "inicio", None))
        fim = attrs.get("fim", getattr(self.instance, "fim", None))
        if inicio is not None and fim is not None and fim <= inicio:
            raise serializers.ValidationError({"fim": "fim deve ser maior que inicio."})

        # Default de rastrear_conclusao herdado da classe no create.
        classe = attrs.get("classe", getattr(self.instance, "classe", None))
        if "rastrear_conclusao" not in attrs and self.instance is None:
            if classe is not None:
                attrs["rastrear_conclusao"] = classe.rastreia_conclusao

        # Coerção de status (Handoff §4.3): centralizada no servidor.
        rastrear = attrs.get(
            "rastrear_conclusao", getattr(self.instance, "rastrear_conclusao", None)
        )
        if rastrear is False:
            attrs["status"] = None
        elif rastrear is True:
            status = attrs.get("status", getattr(self.instance, "status", None))
            if not status:
                attrs["status"] = Evento.Status.AGENDADO
        return attrs

    def create(self, validated_data):
        regra_data = validated_data.pop("regra_recorrencia", None)
        if regra_data:
            validated_data["regra_recorrencia"] = RegraRecorrencia.objects.create(
                **regra_data
            )
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "regra_recorrencia" in validated_data:
            regra_data = validated_data.pop("regra_recorrencia")
            if regra_data is None:
                instance.regra_recorrencia = None
            elif instance.regra_recorrencia is not None:
                for attr, value in regra_data.items():
                    setattr(instance.regra_recorrencia, attr, value)
                instance.regra_recorrencia.save()
            else:
                instance.regra_recorrencia = RegraRecorrencia.objects.create(
                    **regra_data
                )
        return super().update(instance, validated_data)


class PromoverSerializer(serializers.Serializer):
    """Corpo de POST /tarefas/{id}/promover (Handoff §8.2)."""

    inicio = serializers.DateTimeField()
    fim = serializers.DateTimeField(required=False)
    classe_id = serializers.PrimaryKeyRelatedField(
        queryset=Classe.objects.all(), source="classe", required=False
    )

    def validate(self, attrs):
        inicio = attrs.get("inicio")
        fim = attrs.get("fim")
        if fim is not None and fim <= inicio:
            raise serializers.ValidationError({"fim": "fim deve ser maior que inicio."})
        return attrs
