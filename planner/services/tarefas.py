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

from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from planner.models import Classe, Evento, Tarefa

DURACAO_PADRAO = timedelta(hours=1)


class ClasseDesconhecida(ValueError):
    """`classe_id` que não existe. Separada porque quem chama consegue reagir:
    a API devolve 400 no campo, e o agente devolve as classes reais para o
    modelo corrigir a chamada no turno seguinte."""


def criar(dono, titulo, classe_id=None, deadline=None, esforco_min=None, descricao=""):
    """Cria uma Tarefa no Inbox de um perfil, a partir de dados já normalizados.

    Existe para o agente ter o mesmo caminho de escrita da API **sem HTTP**. A
    validação de forma (tipos, obrigatórios) continua no serializer, no caminho
    HTTP; aqui fica a regra de domínio que os dois compartilham.
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

    return Tarefa.objects.create(
        dono=dono,
        titulo=titulo.strip(),
        descricao=descricao or "",
        classe=classe,
        deadline=deadline,
        esforco_estimado=esforco_min,
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
