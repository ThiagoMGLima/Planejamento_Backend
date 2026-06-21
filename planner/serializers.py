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

        # Default: todo evento acompanha conclusão (independe da classe). O
        # cliente pode enviar rastrear_conclusao=false explicitamente para desligar.
        if "rastrear_conclusao" not in attrs and self.instance is None:
            attrs["rastrear_conclusao"] = True

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


class PlanejarSessaoSerializer(serializers.Serializer):
    """Uma sessão de produção (intervalo) do planejamento."""

    inicio = serializers.DateTimeField()
    fim = serializers.DateTimeField()

    def validate(self, attrs):
        if attrs["fim"] <= attrs["inicio"]:
            raise serializers.ValidationError({"fim": "fim deve ser maior que inicio."})
        return attrs


class PlanejarSerializer(serializers.Serializer):
    """Corpo de POST /tarefas/{id}/planejar — divide a produção em N sessões.

    Cada sessão vira um Evento vinculado à tarefa (origem_tarefa). O cliente já
    envia a divisão final (sugerida pelo app e ajustada pelo usuário).
    """

    sessoes = PlanejarSessaoSerializer(many=True)
    classe_id = serializers.PrimaryKeyRelatedField(
        queryset=Classe.objects.all(), source="classe", required=False
    )

    def validate_sessoes(self, value):
        if not value:
            raise serializers.ValidationError("Informe ao menos uma sessão.")
        return value


HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PreferenciasSerializer(serializers.Serializer):
    """Preferências (suaves) do planejamento — todas opcionais, com default."""

    janela_inicio = serializers.RegexField(HHMM, required=False)
    janela_fim = serializers.RegexField(HHMM, required=False)
    evitar_fds = serializers.BooleanField(required=False)
    max_min_por_dia_por_tarefa = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    max_min_por_dia_total = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    sessao_min = serializers.IntegerField(required=False, min_value=1)
    sessao_max = serializers.IntegerField(required=False, min_value=1)
    granularidade_min = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        from .services.planejamento import DEFAULTS, _hhmm_para_min

        janela_inicio = attrs.get("janela_inicio", DEFAULTS["janela_inicio"])
        janela_fim = attrs.get("janela_fim", DEFAULTS["janela_fim"])
        if _hhmm_para_min(janela_inicio) >= _hhmm_para_min(janela_fim):
            raise serializers.ValidationError(
                {"janela_fim": "janela_fim deve ser maior que janela_inicio."}
            )
        sessao_min = attrs.get("sessao_min", DEFAULTS["sessao_min"])
        sessao_max = attrs.get("sessao_max", DEFAULTS["sessao_max"])
        if sessao_min > sessao_max:
            raise serializers.ValidationError(
                {"sessao_min": "sessao_min não pode ser maior que sessao_max."}
            )
        return attrs


class CalcularSerializer(serializers.Serializer):
    """Corpo de POST /planejamento/calcular — preview do plano (não persiste)."""

    tarefa_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    a_partir_de = serializers.DateTimeField(required=False)
    preferencias = PreferenciasSerializer(required=False)


class AplicarSessaoSerializer(serializers.Serializer):
    """Uma sessão revisada, já vinculada à tarefa (origem)."""

    tarefa_id = serializers.UUIDField()
    inicio = serializers.DateTimeField()
    fim = serializers.DateTimeField()

    def validate(self, attrs):
        if attrs["fim"] <= attrs["inicio"]:
            raise serializers.ValidationError({"fim": "fim deve ser maior que inicio."})
        return attrs


class AplicarSerializer(serializers.Serializer):
    """Corpo de POST /planejamento/aplicar — cria os eventos das sessões."""

    sessoes = AplicarSessaoSerializer(many=True)

    def validate_sessoes(self, value):
        if not value:
            raise serializers.ValidationError("Informe ao menos uma sessão.")
        return value
