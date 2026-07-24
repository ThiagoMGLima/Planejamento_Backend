"""Services extraídos das views no PR0 da Fase 0B.

`views.py` tinha regra de negócio dentro de `promover`/`planejar` e das leituras
de agenda — contrariando o "DRF fino" que o CLAUDE.md declara, e impedindo o
agente de reusar a regra sem sair por HTTP. Aqui a lógica é testada direto, sem
passar pelo ciclo de request.

Os testes de API (`test_api.py`) seguem valendo como gabarito de que o
comportamento externo não mudou.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from planner.models import Classe, Evento, Tarefa
from planner.services import agenda, tarefas
from planner.tests.factories import (
    ClasseFactory,
    EventoFactory,
    RegraRecorrenciaFactory,
    TarefaFactory,
    aware,
)


# --------------------------------------------------------------------------- #
# services/tarefas.py                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_promover_usa_esforco_para_o_fim():
    tarefa = TarefaFactory(esforco_estimado=90)
    evento = tarefas.promover(tarefa, inicio=aware(2026, 7, 6, 8))
    assert evento.fim == aware(2026, 7, 6, 9, 30)
    assert evento.origem_tarefa == tarefa
    assert evento.rastrear_conclusao is True
    tarefa.refresh_from_db()
    assert tarefa.status == Tarefa.Status.PROMOVIDA


@pytest.mark.django_db
def test_promover_sem_esforco_usa_uma_hora():
    tarefa = TarefaFactory(esforco_estimado=None)
    evento = tarefas.promover(tarefa, inicio=aware(2026, 7, 6, 8))
    assert evento.fim == aware(2026, 7, 6, 9)


@pytest.mark.django_db
def test_promover_fim_explicito_vence_o_esforco():
    tarefa = TarefaFactory(esforco_estimado=90)
    evento = tarefas.promover(
        tarefa, inicio=aware(2026, 7, 6, 8), fim=aware(2026, 7, 6, 8, 15)
    )
    assert evento.fim == aware(2026, 7, 6, 8, 15)


@pytest.mark.django_db
def test_promover_sem_classe_nenhuma_levanta(perfil):
    tarefa = TarefaFactory(classe=None)
    with pytest.raises(ValueError):
        tarefas.promover(tarefa, inicio=aware(2026, 7, 6, 8))
    assert not Evento.objects.do_dono(perfil).exists()  # transação não deixou lixo


@pytest.mark.django_db
def test_promover_classe_explicita_vence_a_da_tarefa():
    outra = ClasseFactory(nome="Outra")
    tarefa = TarefaFactory()
    evento = tarefas.promover(tarefa, inicio=aware(2026, 7, 6, 8), classe=outra)
    assert evento.classe == outra


@pytest.mark.django_db
def test_planejar_cria_um_evento_por_sessao():
    tarefa = TarefaFactory()
    sessoes = [
        {"inicio": aware(2026, 7, 6, 8), "fim": aware(2026, 7, 6, 9)},
        {"inicio": aware(2026, 7, 7, 8), "fim": aware(2026, 7, 7, 10)},
    ]
    eventos = tarefas.planejar(tarefa, sessoes=sessoes)
    assert len(eventos) == 2
    assert all(e.origem_tarefa == tarefa for e in eventos)
    tarefa.refresh_from_db()
    assert tarefa.status == Tarefa.Status.PROMOVIDA


@pytest.mark.django_db
def test_planejar_sem_classe_nao_cria_nada(perfil):
    tarefa = TarefaFactory(classe=None)
    with pytest.raises(ValueError):
        tarefas.planejar(
            tarefa,
            sessoes=[{"inicio": aware(2026, 7, 6, 8), "fim": aware(2026, 7, 6, 9)}],
        )
    assert not Evento.objects.do_dono(perfil).exists()


@pytest.mark.django_db
def test_criar_tarefa_com_classe_inexistente_levanta_classe_desconhecida(perfil):
    with pytest.raises(tarefas.ClasseDesconhecida):
        tarefas.criar(perfil, "X", classe_id="00000000-0000-0000-0000-000000000000")


@pytest.mark.django_db
def test_criar_tarefa_com_classe_id_que_nem_e_uuid_levanta_o_mesmo_erro(perfil):
    """O 7B às vezes manda o *nome* da classe no lugar do id: `pk=` levanta
    ValidationError (não DoesNotExist), e quem chama precisa do mesmo erro."""
    with pytest.raises(tarefas.ClasseDesconhecida):
        tarefas.criar(perfil, "X", classe_id="Estudar")


@pytest.mark.django_db
def test_criar_tarefa_recusa_titulo_vazio(perfil):
    with pytest.raises(ValueError):
        tarefas.criar(perfil, "   ")


@pytest.mark.django_db
def test_criar_tarefa_recusa_esforco_zero(perfil):
    with pytest.raises(ValueError):
        tarefas.criar(perfil, "X", esforco_min=0)


@pytest.mark.django_db
def test_criar_tarefa_grava_e_normaliza(perfil):
    classe = Classe.objects.do_dono(perfil).get(nome="Estudar")
    tarefa = tarefas.criar(
        perfil, "  Lista 4  ", classe_id=str(classe.id), esforco_min=60, descricao="ok"
    )
    assert tarefa.titulo == "Lista 4"  # strip
    assert tarefa.classe == classe
    assert tarefa.status == Tarefa.Status.INBOX


# --------------------------------------------------------------------------- #
# services/agenda.py                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_janela_invertida_e_grande_demais_sao_recusadas():
    with pytest.raises(agenda.JanelaInvalida):
        agenda.validar_janela(aware(2026, 7, 6), aware(2026, 7, 6))
    with pytest.raises(agenda.JanelaInvalida):
        agenda.validar_janela(aware(2026, 1, 1), aware(2026, 12, 31))


@pytest.mark.django_db
def test_eventos_na_janela_ordena_pelo_inicio_efetivo(perfil):
    EventoFactory(
        titulo="tarde", inicio=aware(2026, 7, 6, 15), fim=aware(2026, 7, 6, 16)
    )
    EventoFactory(titulo="manhã", inicio=aware(2026, 7, 6, 8), fim=aware(2026, 7, 6, 9))

    itens = agenda.eventos_na_janela(perfil, aware(2026, 7, 6), aware(2026, 7, 7))

    assert [i.evento.titulo for i in itens] == ["manhã", "tarde"]
    assert all(i.ocorrencia is None for i in itens)  # nenhum é recorrente


@pytest.mark.django_db
def test_eventos_na_janela_expande_recorrente_sem_materializar(perfil):
    regra = RegraRecorrenciaFactory(tipo="SEMANAL", dias=[0])  # segundas
    EventoFactory(
        titulo="Cálculo",
        inicio=aware(2026, 7, 6, 8),  # 2026-07-06 é segunda
        fim=aware(2026, 7, 6, 10),
        regra_recorrencia=regra,
    )

    itens = agenda.eventos_na_janela(perfil, aware(2026, 7, 6), aware(2026, 7, 28))

    assert [i.ocorrencia.data.isoformat() for i in itens] == [
        "2026-07-06",
        "2026-07-13",
        "2026-07-20",
        "2026-07-27",
    ]
    # Ocorrência não tocada segue virtual: nada foi gravado.
    assert not itens[0].evento.ocorrencias.exists()


@pytest.mark.django_db
def test_eventos_fora_da_janela_ficam_de_fora(perfil):
    EventoFactory(inicio=aware(2026, 8, 1, 8), fim=aware(2026, 8, 1, 9))
    itens = agenda.eventos_na_janela(perfil, aware(2026, 7, 6), aware(2026, 7, 7))
    assert itens == []


@pytest.mark.django_db
def test_pendentes_so_traz_rastreavel_agendado_e_vencido(perfil):
    agora = timezone.now()
    vencido = EventoFactory(
        titulo="vencido",
        inicio=agora - timedelta(hours=3),
        fim=agora - timedelta(hours=2),
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )
    EventoFactory(  # futuro
        titulo="futuro",
        inicio=agora + timedelta(hours=1),
        fim=agora + timedelta(hours=2),
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )
    EventoFactory(  # não rastreável
        titulo="solto",
        inicio=agora - timedelta(hours=3),
        fim=agora - timedelta(hours=2),
        rastrear_conclusao=False,
        status=Evento.Status.AGENDADO,
    )
    EventoFactory(  # já concluído
        titulo="feito",
        inicio=agora - timedelta(hours=3),
        fim=agora - timedelta(hours=2),
        rastrear_conclusao=True,
        status=Evento.Status.CONCLUIDO,
    )

    assert [e.id for e in agenda.pendentes(perfil, agora)] == [vencido.id]
