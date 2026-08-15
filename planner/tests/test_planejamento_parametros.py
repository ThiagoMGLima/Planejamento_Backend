"""Parâmetros de agendamento por tarefa (Fase 1.1, PR A).

Cobre os knobs que a `Tarefa` passou a carregar: `estrategia` CEDO/TARDE, os
pisos/tetos DUROS de data (`nao_antes_de`/`nao_depois_de`) e as restrições
SUAVES de janela/dias. Plano em `docs/tasks/fase1-parametros-por-tarefa.md`.

Três invariantes que estes testes existem para travar:

1. **TARDE ancora no FIM**, e só recua quando falta espaço (nunca por gosto).
2. **Duro não relaxa.** Piso/teto de data sobrevivem aos 6 níveis da cascata;
   se não couber, vira `nao_alocado` — nunca sessão fora do pedido.
3. **Default é comportamento antigo.** Tudo nulo ⇒ plano idêntico ao de antes
   deste PR, sessão a sessão.

O grosso é teste puro (`calcular_plano` não toca no banco); os de serializer e
de leitura dos campos do model usam DB.
"""

from datetime import date, time, timedelta

import pytest

from planner.models import Tarefa
from planner.services import planejamento as P

from .factories import TarefaFactory, aware

# 2026-06-01 é uma segunda-feira (mesma âncora do test_planejamento.py).
SEG = aware(2026, 6, 1, 8)


def _tarefa(id, esforco, deadline, classe_id="c1", titulo=None, **knobs):
    return P.TarefaEntrada(
        id=id,
        titulo=titulo or f"Tarefa {id}",
        classe_id=classe_id,
        esforco=esforco,
        deadline=deadline,
        **knobs,
    )


def _total(sessoes, tarefa_id):
    return sum(s.dur_min for s in sessoes if s.tarefa_id == tarefa_id)


def _sem_sobreposicao(sessoes):
    iv = sorted((s.inicio, s.fim) for s in sessoes)
    return all(iv[i][1] <= iv[i + 1][0] for i in range(len(iv) - 1))


# --------------------------------------------------------------------------- #
# TARDE — o núcleo do PR                                                       #
# --------------------------------------------------------------------------- #
def test_tarde_cola_a_ultima_sessao_na_deadline():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)  # sexta 18:00
    t = _tarefa("A", 120, deadline, estrategia=P.TARDE)
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert nao == []
    assert _total(sessoes, "A") == 120
    assert max(s.fim for s in sessoes) == deadline


def test_tarde_e_cedo_ocupam_extremos_opostos_da_janela():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)
    cedo = _tarefa("A", 120, deadline)
    tarde = _tarefa("A", 120, deadline, estrategia=P.TARDE)
    s_cedo, _ = P.calcular_plano([cedo], [], prefs, SEG, deadline)
    s_tarde, _ = P.calcular_plano([tarde], [], prefs, SEG, deadline)
    assert min(s.inicio for s in s_tarde) > min(s.inicio for s in s_cedo)
    assert max(s.fim for s in s_tarde) > max(s.fim for s in s_cedo)


def test_tarde_soma_exata_e_sem_sobreposicao():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 20)
    t = _tarefa("A", 400, deadline, estrategia=P.TARDE)
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert _total(sessoes, "A") == 400
    assert nao == []
    assert _sem_sobreposicao(sessoes)


def test_tarde_nunca_ultrapassa_a_deadline():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 4, 15, 30)  # quinta 15:30, fora do grid "redondo"
    t = _tarefa("A", 200, deadline, estrategia=P.TARDE)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert all(s.fim <= deadline for s in sessoes)


def test_tarde_snapa_o_fim_no_grid_da_granularidade():
    """Deadline fora do grid não pode gerar sessão em horário quebrado."""
    prefs, _ = P.montar_preferencias({})  # granularidade 15min
    deadline = aware(2026, 6, 4, 15, 7)
    t = _tarefa("A", 60, deadline, estrategia=P.TARDE)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    for s in sessoes:
        assert s.inicio.minute % 15 == 0, s.inicio
        assert s.fim.minute % 15 == 0, s.fim


def test_tarde_desvia_de_evento_ocupado():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)
    ocupado = [(aware(2026, 6, 5, 15), aware(2026, 6, 5, 18))]
    t = _tarefa("A", 60, deadline, estrategia=P.TARDE)
    sessoes, _ = P.calcular_plano([t], ocupado, prefs, SEG, deadline)
    assert _total(sessoes, "A") == 60
    for s in sessoes:
        assert s.fim <= ocupado[0][0] or s.inicio >= ocupado[0][1]
    # Encostou no evento pelo lado de trás, que é o mais tarde possível.
    assert max(s.fim for s in sessoes) == ocupado[0][0]


def test_tarde_recua_apenas_o_necessario_quando_falta_espaco():
    """Esforço grande obriga a voltar no tempo — mas só o que não coube."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)  # sexta
    t = _tarefa("A", 480, deadline, estrategia=P.TARDE)  # 4 dias de teto (120/dia)
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert nao == []
    assert _total(sessoes, "A") == 480
    dias = {s.inicio.date() for s in sessoes}
    # Usou os dias colados na deadline, não os do começo da janela.
    assert date(2026, 6, 5) in dias
    assert date(2026, 6, 1) not in dias


# --------------------------------------------------------------------------- #
# D1 — TARDE e buffer_dias são ortogonais                                      #
# --------------------------------------------------------------------------- #
def test_tarde_ignora_buffer_dias():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)
    t = _tarefa("A", 60, deadline, estrategia=P.TARDE, buffer_dias=3)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    # Sem o desacoplamento, o buffer teria puxado tudo para 02/06.
    assert max(s.fim for s in sessoes) == deadline


def test_cedo_continua_respeitando_buffer_dias():
    """Guarda da outra metade da D1: o desacoplamento não pode vazar para CEDO."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 18)
    t = _tarefa("A", 60, deadline, buffer_dias=3)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert all(s.fim <= deadline - timedelta(days=3) for s in sessoes)


def test_tarde_com_nao_depois_de_termina_na_vespera():
    """Como se expressa 'terminar antes do prazo' agora que o buffer não serve."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)  # sexta
    t = _tarefa("A", 60, deadline, estrategia=P.TARDE, nao_depois_de=date(2026, 6, 4))
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert all(s.inicio.date() <= date(2026, 6, 4) for s in sessoes)
    assert max(s.fim for s in sessoes).date() == date(2026, 6, 4)


# --------------------------------------------------------------------------- #
# Mistura CEDO/TARDE (D8: guloso mantido)                                      #
# --------------------------------------------------------------------------- #
def test_mistura_cedo_e_tarde_nao_gera_conflito():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)
    a = _tarefa("A", 240, deadline)
    b = _tarefa("B", 240, deadline, estrategia=P.TARDE)
    sessoes, nao = P.calcular_plano([a, b], [], prefs, SEG, deadline)
    assert nao == []
    assert _total(sessoes, "A") == 240
    assert _total(sessoes, "B") == 240
    assert _sem_sobreposicao(sessoes)


# --------------------------------------------------------------------------- #
# Limites DUROS de data — não relaxam                                          #
# --------------------------------------------------------------------------- #
def test_nao_antes_de_empurra_o_inicio():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 18)
    t = _tarefa("A", 120, deadline, nao_antes_de=date(2026, 6, 8))
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert sessoes
    assert all(s.inicio.date() >= date(2026, 6, 8) for s in sessoes)


def test_nao_depois_de_e_inclusivo():
    """O dia do teto ainda pode receber sessão — o corte é a meia-noite seguinte."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 18)
    t = _tarefa("A", 60, deadline, estrategia=P.TARDE, nao_depois_de=date(2026, 6, 3))
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert sessoes
    assert max(s.fim for s in sessoes).date() == date(2026, 6, 3)


def test_limites_duros_sobrevivem_a_cascata_de_relaxamento():
    """Esforço que não cabe na janela dura vira nao_alocado, não sessão fora dela."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 30, 18)
    # 1 dia útil disponível (03/06, quarta) × 24h teóricas, pedindo 3000 min.
    t = _tarefa(
        "A",
        3000,
        deadline,
        nao_antes_de=date(2026, 6, 3),
        nao_depois_de=date(2026, 6, 3),
    )
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert all(s.inicio.date() == date(2026, 6, 3) for s in sessoes)
    assert nao and nao[0].minutos_restantes > 0


def test_janela_dura_vazia_gera_nao_alocado_com_motivo_proprio():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 18)
    t = _tarefa(
        "A",
        60,
        deadline,
        nao_antes_de=date(2026, 6, 9),
        nao_depois_de=date(2026, 6, 5),  # teto antes do piso
    )
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert sessoes == []
    assert len(nao) == 1
    assert "nao_antes_de" in nao[0].motivo
    assert nao[0].minutos_restantes == 60


# --------------------------------------------------------------------------- #
# Restrições SUAVES de janela/dias                                             #
# --------------------------------------------------------------------------- #
def test_janela_da_tarefa_restringe_dentro_da_global():
    prefs, _ = P.montar_preferencias({})  # global 08:00–22:00
    deadline = aware(2026, 6, 10, 18)
    t = _tarefa("A", 120, deadline, janela_inicio_min=8 * 60, janela_fim_min=12 * 60)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert sessoes
    for s in sessoes:
        assert 8 <= s.inicio.hour < 12
        assert s.fim.hour <= 12


def test_janela_da_tarefa_nao_amplia_a_global():
    """Pedir 06:00–23:00 numa global 08:00–22:00 não abre nada a mais."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 18)
    t = _tarefa("A", 120, deadline, janela_inicio_min=6 * 60, janela_fim_min=23 * 60)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    for s in sessoes:
        assert s.inicio.hour >= 8
        assert s.fim.hour <= 22


def test_dias_permitidos_filtra_os_dias_da_semana():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 30, 18)
    t = _tarefa("A", 240, deadline, dias_permitidos=frozenset({2}))  # só quarta
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert sessoes
    assert {s.inicio.weekday() for s in sessoes} == {2}


def test_restricao_suave_cede_no_nivel_3():
    """Janela apertada + esforço grande: relaxa em vez de virar nao_alocado."""
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 3, 22)  # quarta
    t = _tarefa(
        "A",
        600,
        deadline,
        janela_inicio_min=8 * 60,
        janela_fim_min=9 * 60,  # 1h/dia por 2 dias úteis não dá 600min
    )
    sessoes, nao = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert _total(sessoes, "A") == 600
    assert nao == []
    assert any(s.inicio.hour >= 9 for s in sessoes)  # passou da janela pedida


# --------------------------------------------------------------------------- #
# Defaults preservam o comportamento anterior                                  #
# --------------------------------------------------------------------------- #
def test_knobs_nulos_reproduzem_o_plano_antigo():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 10, 18)
    sem = _tarefa("A", 300, deadline)
    com_cedo = _tarefa("A", 300, deadline, estrategia="CEDO")
    s1, n1 = P.calcular_plano([sem], [], prefs, SEG, deadline)
    s2, n2 = P.calcular_plano([com_cedo], [], prefs, SEG, deadline)
    assert [(s.inicio, s.fim, s.dur_min) for s in s1] == [
        (s.inicio, s.fim, s.dur_min) for s in s2
    ]
    assert n1 == n2


def test_estrategia_nula_nao_e_tarde():
    prefs, _ = P.montar_preferencias({})
    deadline = aware(2026, 6, 5, 18)
    t = _tarefa("A", 60, deadline)
    sessoes, _ = P.calcular_plano([t], [], prefs, SEG, deadline)
    assert min(s.inicio for s in sessoes).date() == date(2026, 6, 1)


# --------------------------------------------------------------------------- #
# Snap para baixo (unitário)                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "minuto,esperado",
    [(0, 0), (7, 0), (15, 15), (29, 15), (44, 30), (59, 45)],
)
def test_snap_abaixo_arredonda_para_tras(minuto, esperado):
    from django.utils import timezone

    tz = timezone.get_current_timezone()
    dt = aware(2026, 6, 1, 10, minuto)
    assert P._snap_abaixo(dt, 15, tz).minute == esperado


# --------------------------------------------------------------------------- #
# Leitura dos campos do model (DB)                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_montar_plano_le_os_parametros_da_tarefa(perfil):
    t = TarefaFactory(
        dono=perfil,
        deadline=aware(2026, 6, 10, 18),
        esforco_estimado=120,
        estrategia=Tarefa.Estrategia.TARDE,
        janela_inicio=time(14, 0),
        janela_fim=time(18, 0),
        dias_permitidos=[3],  # quinta
    )
    res = P.montar_plano(perfil, [t], SEG, {})
    assert res.tarefas[0].estrategia == "TARDE"
    assert res.tarefas[0].janela_inicio_min == 14 * 60
    assert res.tarefas[0].dias_permitidos == frozenset({3})
    for s in res.sessoes:
        assert s.inicio.weekday() == 3
        assert 14 <= s.inicio.hour < 18


@pytest.mark.django_db
def test_tarefa_nova_nasce_sem_estrategia(perfil):
    """D2: não existe default herdado — nem de classe, nem do model."""
    t = TarefaFactory(dono=perfil)
    assert t.estrategia is None
    assert t.nao_antes_de is None
    assert t.dias_permitidos is None


# --------------------------------------------------------------------------- #
# Contrato da API (D4: os campos SAEM no serializer)                           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.mark.django_db
def test_campos_aparecem_na_api(api, perfil):
    t = TarefaFactory(dono=perfil, estrategia=Tarefa.Estrategia.TARDE)
    corpo = api.get(f"/api/v1/tarefas/{t.id}/").json()
    assert corpo["estrategia"] == "TARDE"
    for campo in (
        "nao_antes_de",
        "nao_depois_de",
        "janela_inicio",
        "janela_fim",
        "dias_permitidos",
    ):
        assert campo in corpo


@pytest.mark.django_db
def test_patch_grava_os_parametros(api, perfil):
    t = TarefaFactory(dono=perfil)
    resp = api.patch(
        f"/api/v1/tarefas/{t.id}/",
        {
            "estrategia": "TARDE",
            "nao_antes_de": "2026-10-19",
            "janela_inicio": "08:00",
            "janela_fim": "12:00",
            "dias_permitidos": [0, 1, 2],
        },
        format="json",
    )
    assert resp.status_code == 200
    t.refresh_from_db()
    assert t.estrategia == "TARDE"
    assert t.nao_antes_de == date(2026, 10, 19)
    assert t.janela_inicio == time(8, 0)
    assert t.dias_permitidos == [0, 1, 2]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload,campo",
    [
        ({"janela_inicio": "08:00"}, "janela_inicio"),  # sem o par
        ({"janela_inicio": "18:00", "janela_fim": "09:00"}, "janela_fim"),  # invertida
        ({"janela_inicio": "09:00", "janela_fim": "09:00"}, "janela_fim"),  # vazia
        ({"dias_permitidos": []}, "dias_permitidos"),  # proibiria tudo
        ({"dias_permitidos": [0, 9]}, "dias_permitidos"),  # fora de 0..6
        (
            {"nao_antes_de": "2026-10-20", "nao_depois_de": "2026-10-10"},
            "nao_depois_de",
        ),
    ],
)
def test_parametros_incoerentes_dao_400(api, perfil, payload, campo):
    t = TarefaFactory(dono=perfil)
    resp = api.patch(f"/api/v1/tarefas/{t.id}/", payload, format="json")
    assert resp.status_code == 400
    assert campo in resp.json()


@pytest.mark.django_db
def test_estrategia_invalida_da_400(api, perfil):
    t = TarefaFactory(dono=perfil)
    resp = api.patch(f"/api/v1/tarefas/{t.id}/", {"estrategia": "ASAP"}, format="json")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Replanejar preserva os parâmetros                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_replanejar_mantem_a_estrategia_da_tarefa(perfil):
    """O pool do replanejar monta SimpleNamespace; sem os campos, TARDE sumiria."""
    from planner.models import Evento
    from planner.services import replanejamento as R

    from .factories import EventoFactory

    tarefa = TarefaFactory(
        dono=perfil,
        esforco_estimado=120,
        deadline=aware(2026, 6, 10, 18),
        status=Tarefa.Status.PROMOVIDA,
        estrategia=Tarefa.Estrategia.TARDE,
        nao_antes_de=date(2026, 6, 8),
    )
    EventoFactory(
        dono=perfil,
        titulo=tarefa.titulo,
        classe=tarefa.classe,
        origem_tarefa=tarefa,
        inicio=aware(2026, 6, 3, 10),
        fim=aware(2026, 6, 3, 12),
        status=Evento.Status.AGENDADO,
    )
    futuras = R._sessoes_futuras(perfil, SEG)
    pool, _ = R._pool_e_substituiveis(perfil, SEG, futuras)
    assert pool and pool[0].estrategia == "TARDE"
    assert pool[0].nao_antes_de == date(2026, 6, 8)

    rp = R.replanejar(perfil, agora=SEG)
    assert rp.res.sessoes
    assert all(s.inicio.date() >= date(2026, 6, 8) for s in rp.res.sessoes)


# --------------------------------------------------------------------------- #
# Comando de marcação (Pendência 1 do plano)                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_comando_sem_aplicar_nao_grava(perfil):
    from io import StringIO

    from django.core.management import call_command

    from planner.models import Classe

    # As 5 classes padrão já vêm semeadas com o perfil — criar outra "Estudar"
    # esbarraria na unicidade por-dono.
    classe = Classe.objects.do_dono(perfil).get(nome="Estudar")
    t = TarefaFactory(dono=perfil, classe=classe)
    saida = StringIO()
    call_command(
        "marcar_estrategia",
        "--classe",
        "Estudar",
        "--estrategia",
        "TARDE",
        stdout=saida,
    )
    t.refresh_from_db()
    assert t.estrategia is None
    assert "Simulação" in saida.getvalue()


@pytest.mark.django_db
def test_comando_com_aplicar_grava_e_pula_quem_ja_tem(perfil):
    from io import StringIO

    from django.core.management import call_command

    from planner.models import Classe

    classe = Classe.objects.do_dono(perfil).get(nome="Estudar")
    virgem = TarefaFactory(dono=perfil, classe=classe)
    ja_marcada = TarefaFactory(
        dono=perfil, classe=classe, estrategia=Tarefa.Estrategia.CEDO
    )
    call_command(
        "marcar_estrategia",
        "--classe",
        "Estudar",
        "--estrategia",
        "TARDE",
        "--aplicar",
        stdout=StringIO(),
    )
    virgem.refresh_from_db()
    ja_marcada.refresh_from_db()
    assert virgem.estrategia == "TARDE"
    assert ja_marcada.estrategia == "CEDO"  # não sobrescreve sem --sobrescrever


@pytest.mark.django_db
def test_comando_exige_filtro(perfil):
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("marcar_estrategia", "--estrategia", "TARDE", stdout=StringIO())
