"""Transições da Tarefa: Inbox → calendário (PR0 da Fase 0B).

A regra vivia dentro de `TarefaViewSet.promover`/`planejar`. Desceu para cá por
dois motivos:

1. O `CLAUDE.md` já declara "DRF fino: as views delegam para services" — a view
   era a exceção que contrariava a própria arquitetura.
2. As ferramentas do agente precisam desta regra **em processo**. Enquanto ela
   morava na view, o único jeito de o agente usá-la era HTTP contra a própria
   API — ver `services/agente.py`.

Convenção de erro: estes services levantam `ValueError` com uma mensagem de
domínio. Quem traduz para HTTP é a view; o agente traduz para dict acionável.
"""

import unicodedata
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from planner.models import Classe, Evento, Tarefa

DURACAO_PADRAO = timedelta(hours=1)


class ClasseDesconhecida(ValueError):
    """`classe_id` que não existe. Separada porque quem chama consegue reagir:
    a API devolve 400 no campo, e o agente devolve as classes reais para o
    modelo corrigir a chamada no turno seguinte."""


def _normalizar_titulo(texto):
    """Minúsculas, sem acento e com espaços colapsados — para comparar títulos."""
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or "").casefold())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.split())


# Abaixo disto, "conter" não diz nada: "prova" está dentro de meia agenda.
_MIN_TITULO_PARECIDO = 10


def parecidas(dono, titulo):
    """Tarefas cujo título contém o novo (ou está contido nele).

    Serve à recusa acionável de `criar_tarefa`: no dogfooding de 15/08/2026 o
    modelo tentou "alterar" tarefas chamando criar, e os títulos que ele mandou
    eram PREFIXOS dos reais ("Estudar para a PP1" vs "Estudar para a PP1 —
    diodos e transistor bipolar"). Comparação exata não pegaria nenhum dos dois.

    Deliberadamente burro: contido/contém sobre o título normalizado. Não é
    similaridade semântica — é o suficiente para o caso que aconteceu, e o que
    não pega segue criando normalmente.
    """
    alvo = _normalizar_titulo(titulo)
    if len(alvo) < _MIN_TITULO_PARECIDO:
        return []
    achadas = []
    for t in Tarefa.objects.do_dono(dono).only("id", "titulo", "deadline"):
        existente = _normalizar_titulo(t.titulo)
        if alvo in existente or existente in alvo:
            achadas.append(t)
    return achadas


class TarefaDesconhecida(ValueError):
    """Tarefa inexistente — ou de outro perfil, que dá no mesmo por fora.

    A busca é escopada, então a tarefa alheia simplesmente não é encontrada. A
    resposta não deve permitir distinguir "não existe" de "existe e não é sua".
    """


class ParametrosInvalidos(ValueError):
    """Parâmetros de agendamento incoerentes. `erros` já no shape de 400 da API."""

    def __init__(self, erros):
        super().__init__(str(erros))
        self.erros = erros


def validar_parametros_agendamento(valores):
    """Coerência dos parâmetros de agendamento (Fase 1.1). Levanta ou volta calado.

    **Fonte única.** Mora aqui, e não no serializer, porque a API e a ferramenta
    do agente escrevem os mesmos campos por caminhos diferentes; regra duplicada
    é regra que diverge. O serializer chama esta função (a direção da dependência
    já é essa — `serializers` importa de `services`, nunca o contrário).

    Valida só o que é **contraditório na entrada**. "Não coube" não é erro: o
    solver resolve em `nao_alocado`. O que barramos é o que nunca poderia dar
    certo, para o erro aparecer na escrita e não num plano silenciosamente vazio.

    `valores` deve trazer o estado **efetivo** de cada campo (o que já está na
    tarefa, sobrescrito pelo que está sendo alterado) — validar só o delta
    deixaria passar uma combinação inválida formada com o que já estava lá.
    """
    ini, fim = valores.get("janela_inicio"), valores.get("janela_fim")
    if (ini is None) != (fim is None):
        raise ParametrosInvalidos(
            {"janela_inicio": ["janela_inicio e janela_fim andam juntas."]}
        )
    if ini is not None and ini >= fim:
        raise ParametrosInvalidos(
            {"janela_fim": ["janela_fim deve ser maior que janela_inicio."]}
        )

    antes, depois = valores.get("nao_antes_de"), valores.get("nao_depois_de")
    if antes and depois and antes > depois:
        raise ParametrosInvalidos(
            {"nao_depois_de": ["nao_depois_de não pode ser antes de nao_antes_de."]}
        )

    dias = valores.get("dias_permitidos")
    if dias is not None:
        if not dias:
            raise ParametrosInvalidos(
                {"dias_permitidos": ["Lista vazia proibiria todos os dias; use null."]}
            )
        if any(d > 6 for d in dias):
            raise ParametrosInvalidos(
                {"dias_permitidos": ["Dias vão de 0 (segunda) a 6 (domingo)."]}
            )

    estrategia = valores.get("estrategia")
    if estrategia is not None and estrategia not in Tarefa.Estrategia.values:
        raise ParametrosInvalidos(
            {
                "estrategia": [
                    f"Deve ser {' ou '.join(Tarefa.Estrategia.values)}, ou nulo."
                ]
            }
        )


# Campos que `atualizar` aceita. `titulo` e `descricao` ficam DE FORA de
# propósito: são texto do usuário, e a `descricao` virou entrada de planejamento
# (PR C) — deixar a IA reescrevê-los seria ela editar o próprio insumo, sem que
# ninguém percebesse. Quem muda esses dois é o usuário, pela API.
CAMPOS_ATUALIZAVEIS = (
    "deadline",
    "esforco_estimado",
    "estrategia",
    "nao_antes_de",
    "nao_depois_de",
    "janela_inicio",
    "janela_fim",
    "dias_permitidos",
)


def atualizar(dono, tarefa_id, **campos):
    """Altera campos de agendamento de uma tarefa existente, no escopo do dono.

    Existe porque o agente não tinha como EDITAR: pedido de "faça esta tarefa
    terminar na véspera" virava `criar_tarefa`, e o modelo duplicava a tarefa em
    vez de ajustá-la (visto no dogfooding de 15/08/2026). Não era limitação do
    modelo — era ferramenta faltando.

    Campo ausente do kwargs fica como está; passar `None` **limpa** o campo (é
    como se desfaz uma restrição). Só `CAMPOS_ATUALIZAVEIS` são aceitos.
    """
    try:
        tarefa = Tarefa.objects.do_dono(dono).get(pk=tarefa_id)
    except (Tarefa.DoesNotExist, DjangoValidationError):
        # ValidationError: `pk=` com algo que nem é UUID (o 7B manda título).
        raise TarefaDesconhecida(f"Tarefa {tarefa_id!r} não existe.")

    desconhecidos = sorted(set(campos) - set(CAMPOS_ATUALIZAVEIS))
    if desconhecidos:
        raise ParametrosInvalidos(
            {c: ["Campo não atualizável por aqui."] for c in desconhecidos}
        )

    if "esforco_estimado" in campos:
        esforco = campos["esforco_estimado"]
        if esforco is not None and int(esforco) < 1:
            raise ParametrosInvalidos(
                {"esforco_estimado": ["Deve ser um inteiro ≥ 1."]}
            )

    # Estado EFETIVO: o que já existe, coberto pelo que está mudando.
    efetivo = {c: getattr(tarefa, c) for c in CAMPOS_ATUALIZAVEIS}
    efetivo.update(campos)
    validar_parametros_agendamento(efetivo)

    for campo, valor in campos.items():
        setattr(tarefa, campo, valor)
    tarefa.save(update_fields=[*campos, "atualizado_em"])
    return tarefa


def criar(
    dono,
    titulo,
    classe_id=None,
    deadline=None,
    esforco_min=None,
    descricao="",
    estrategia=None,
):
    """Cria uma Tarefa no Inbox de um perfil, a partir de dados já normalizados.

    Existe para o agente ter o mesmo caminho de escrita da API **sem HTTP**. A
    validação de forma (tipos, obrigatórios) continua no serializer, no caminho
    HTTP; aqui fica a regra de domínio que os dois compartilham.

    `estrategia` (Fase 1.1) entra aqui porque **não há default herdado de classe**
    (decisão D2): sem poder setá-la na criação, toda tarefa que o agente cria
    nasceria sem estratégia, e estudo de prova voltaria a ser agendado meses
    antes. Os demais knobs (janelas, datas-limite) ficam de fora de propósito —
    quem os infere é a camada de texto livre, a partir da `descricao`.
    """
    if not (titulo or "").strip():
        raise ValueError("titulo é obrigatório.")

    classe = None
    if classe_id is not None:
        try:
            # Escopado: sem isto, o agente anexaria a classe de outro perfil.
            classe = Classe.objects.do_dono(dono).get(pk=classe_id)
        except Classe.DoesNotExist:
            raise ClasseDesconhecida(f"Classe {classe_id} não existe.")
        except DjangoValidationError:
            # `pk=` com algo que nem é UUID levanta ValidationError, não
            # DoesNotExist — o 7B às vezes manda o *nome* da classe.
            raise ClasseDesconhecida(f"Classe {classe_id!r} não é um id válido.")

    if esforco_min is not None and int(esforco_min) < 1:
        raise ValueError("esforco_estimado deve ser um inteiro ≥ 1.")

    if estrategia is not None and estrategia not in Tarefa.Estrategia.values:
        raise ValueError(
            f"estrategia deve ser {' ou '.join(Tarefa.Estrategia.values)}."
        )

    return Tarefa.objects.create(
        dono=dono,
        titulo=titulo.strip(),
        descricao=descricao or "",
        classe=classe,
        deadline=deadline,
        esforco_estimado=esforco_min,
        estrategia=estrategia,
    )


def resolver_classe(tarefa, classe=None):
    """Classe explícita > classe da tarefa. Sem nenhuma das duas, é erro.

    Extraído porque `promover` e `planejar` faziam exatamente a mesma checagem,
    palavra por palavra.
    """
    escolhida = classe or tarefa.classe
    if escolhida is None:
        raise ValueError("Tarefa sem classe; informe classe_id.")
    if escolhida.dono_id != tarefa.dono_id:
        # Cinto de segurança: no caminho HTTP o serializer já escopa o
        # `classe_id`. Aqui a checagem vale para quem chama o service direto —
        # o agente, os seeds, o próximo service que ainda não existe.
        raise ClasseDesconhecida(f"Classe {escolhida.id} não existe.")
    return escolhida


@transaction.atomic
def promover(tarefa, inicio, fim=None, classe=None):
    """Arrasto Inbox → calendário (Handoff §8.2). Cria 1 evento.

    `fim` ausente: usa o esforço estimado da tarefa; sem esforço, 1 hora.
    """
    classe = resolver_classe(tarefa, classe)
    if fim is None:
        if tarefa.esforco_estimado:
            fim = inicio + timedelta(minutes=tarefa.esforco_estimado)
        else:
            fim = inicio + DURACAO_PADRAO

    evento = Evento.objects.create(
        # O dono vem da tarefa, nunca de quem chamou: um evento não tem como
        # nascer num perfil diferente do da sua origem.
        dono=tarefa.dono,
        titulo=tarefa.titulo,
        descricao=tarefa.descricao,
        inicio=inicio,
        fim=fim,
        classe=classe,
        # Default: todo evento acompanha conclusão (independe da classe).
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
        origem_tarefa=tarefa,
    )
    _marcar_promovida(tarefa)
    return evento


@transaction.atomic
def planejar(tarefa, sessoes, classe=None):
    """Divide a produção de um To Do em N eventos-sessão.

    Recebe a divisão final (sugerida pelo app, ajustada pelo usuário) e cria um
    Evento por sessão, todos vinculados à tarefa (`origem_tarefa`). A soma das
    sessões é o tempo de produção; cada uma acompanha conclusão.
    """
    classe = resolver_classe(tarefa, classe)
    eventos = [
        Evento.objects.create(
            dono=tarefa.dono,
            titulo=tarefa.titulo,
            descricao=tarefa.descricao,
            inicio=s["inicio"],
            fim=s["fim"],
            classe=classe,
            rastrear_conclusao=True,
            status=Evento.Status.AGENDADO,
            origem_tarefa=tarefa,
        )
        for s in sessoes
    ]
    _marcar_promovida(tarefa)
    return eventos


def _marcar_promovida(tarefa):
    tarefa.status = Tarefa.Status.PROMOVIDA
    tarefa.save(update_fields=["status", "atualizado_em"])
