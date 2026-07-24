"""Isolamento entre contas (Fase 0B, PR1) — os únicos testes que o provam.

**O resto da suíte não serve de gabarito aqui.** Ela roda com um perfil só, onde
"todos os dados" e "os dados do dono" são o mesmo conjunto: passaria verde com
um vazamento dentro. Estes testes existem porque precisam de **dois** perfis
para significar alguma coisa.

Enquanto não há login, quem faz a requisição é sempre o perfil local
(`perfil`); `outro_perfil` é o vizinho, que nunca deveria aparecer. Quando o
PR2 trocar `perfil_do_request`, estes testes continuam valendo palavra por
palavra — muda só quem responde "quem é você".

Duas escolhas de resposta aparecem várias vezes aqui e são deliberadas:

- **404, não 403**, para objeto de outro perfil: quem pergunta não deve
  conseguir distinguir "não existe" de "existe e não é seu".
- **400 "objeto inexistente"** para FK cruzada, pelo mesmo motivo.
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from planner import tasks, views
from planner.managers import EscopoAusente
from planner.models import Classe, Evento, PesoPreferencia, Tarefa
from planner.services import (
    adaptacao,
    agenda,
    aplicacao,
    completion,
    holidays,
    planejamento,
    replanejamento,
)
from planner.tests.factories import (
    ClasseFactory,
    EventoFactory,
    RegraRecorrenciaFactory,
    TarefaFactory,
    aware,
)

pytestmark = pytest.mark.django_db

SEG = aware(2026, 6, 1, 8)  # segunda-feira


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture(autouse=True)
def _cache_limpo(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "isolamento-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()


# --------------------------------------------------------------------------- #
# 1. O default: consulta sem escopo é recusada (0B.10)                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "consulta",
    [
        lambda: list(Evento.objects.all()),
        lambda: Evento.objects.count(),
        lambda: Evento.objects.exists(),
        lambda: Tarefa.objects.filter(titulo="x").first(),
        lambda: Classe.objects.get(nome="Estudar"),
        lambda: Evento.objects.filter(titulo="x").delete(),
        lambda: Tarefa.objects.filter(titulo="x").update(titulo="y"),
    ],
)
def test_consulta_sem_escopo_levanta(consulta):
    """O ponto central do PR: esquecer o escopo é erro em teste, não vazamento
    silencioso. Cobre também os caminhos que não passam por `_fetch_all`."""
    with pytest.raises(EscopoAusente):
        consulta()


def test_sem_escopo_e_do_dono_funcionam(perfil, outro_perfil):
    TarefaFactory(dono=perfil, titulo="minha")
    TarefaFactory(dono=outro_perfil, titulo="dele")

    assert [t.titulo for t in Tarefa.objects.do_dono(perfil)] == ["minha"]
    assert Tarefa.objects.sem_escopo().count() == 2


def test_do_dono_none_e_recusado(perfil):
    """`do_dono(None)` não pode virar "sem filtro": é o modo de o `if dono:`
    de um contextvar vazar sem levantar — a falha que o desenho descartou."""
    with pytest.raises(EscopoAusente):
        list(Tarefa.objects.do_dono(None))


# --------------------------------------------------------------------------- #
# 2. CRUD pela API: o vizinho não existe                                       #
# --------------------------------------------------------------------------- #
def _do_outro(outro_perfil):
    """Um conjunto completo de dados do vizinho."""
    classe = ClasseFactory(dono=outro_perfil, nome="Classe do outro")
    tarefa = TarefaFactory(dono=outro_perfil, classe=classe, titulo="Tarefa do outro")
    evento = EventoFactory(
        dono=outro_perfil,
        classe=classe,
        titulo="Evento do outro",
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )
    return classe, tarefa, evento


@pytest.mark.parametrize("recurso", ["classes", "tarefas", "eventos"])
def test_listar_nao_traz_dados_do_outro(api, perfil, outro_perfil, recurso):
    _do_outro(outro_perfil)
    if recurso == "eventos":
        resp = api.get(
            "/api/v1/eventos/",
            {"inicio": "2026-06-01T00:00:00-03:00", "fim": "2026-06-02T00:00:00-03:00"},
        )
        corpo = resp.json()
    else:
        resp = api.get(f"/api/v1/{recurso}/")
        corpo = resp.json()["results"]
    assert resp.status_code == 200
    assert not any("outro" in str(item) for item in corpo)


def test_ler_por_id_do_outro_da_404(api, perfil, outro_perfil):
    classe, tarefa, evento = _do_outro(outro_perfil)
    assert api.get(f"/api/v1/classes/{classe.id}/").status_code == 404
    assert api.get(f"/api/v1/tarefas/{tarefa.id}/").status_code == 404
    assert api.get(f"/api/v1/eventos/{evento.id}/").status_code == 404


def test_atualizar_e_apagar_do_outro_da_404_e_nao_muda_nada(api, perfil, outro_perfil):
    classe, tarefa, evento = _do_outro(outro_perfil)

    assert (
        api.patch(
            f"/api/v1/tarefas/{tarefa.id}/", {"titulo": "invadido"}, format="json"
        ).status_code
        == 404
    )
    assert api.delete(f"/api/v1/eventos/{evento.id}/").status_code == 404
    assert api.delete(f"/api/v1/classes/{classe.id}/").status_code == 404

    tarefa.refresh_from_db()
    assert tarefa.titulo == "Tarefa do outro"
    assert Evento.objects.do_dono(outro_perfil).filter(id=evento.id).exists()
    assert Classe.objects.do_dono(outro_perfil).filter(id=classe.id).exists()


def test_acoes_sobre_objeto_do_outro_dao_404(api, perfil, outro_perfil):
    _, tarefa, evento = _do_outro(outro_perfil)

    assert (
        api.post(
            f"/api/v1/tarefas/{tarefa.id}/promover/",
            {"inicio": "2026-06-01T08:00:00-03:00"},
            format="json",
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/v1/tarefas/{tarefa.id}/planejar/",
            {
                "sessoes": [
                    {
                        "inicio": "2026-06-01T08:00:00-03:00",
                        "fim": "2026-06-01T09:00:00-03:00",
                    }
                ]
            },
            format="json",
        ).status_code
        == 404
    )
    assert api.post(f"/api/v1/eventos/{evento.id}/concluir/").status_code == 404
    assert api.post(f"/api/v1/eventos/{evento.id}/remarcar/").status_code == 404

    evento.refresh_from_db()
    assert evento.status == Evento.Status.AGENDADO  # nenhuma ação pegou


def test_pendentes_nao_vaza_o_vencido_do_outro(api, perfil, outro_perfil):
    agora = timezone.now()
    EventoFactory(
        dono=outro_perfil,
        titulo="vencido do outro",
        inicio=agora - timedelta(hours=3),
        fim=agora - timedelta(hours=2),
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )
    resp = api.get("/api/v1/pendentes")
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------------- #
# 3. FK cruzada — o vazamento mais fácil de deixar passar                      #
# --------------------------------------------------------------------------- #
def test_criar_evento_com_classe_do_outro_da_400(api, perfil, outro_perfil):
    classe_alheia = ClasseFactory(dono=outro_perfil, nome="Alheia")
    resp = api.post(
        "/api/v1/eventos/",
        {
            "titulo": "meu evento",
            "inicio": "2026-06-01T08:00:00-03:00",
            "fim": "2026-06-01T09:00:00-03:00",
            "classe_id": str(classe_alheia.id),
        },
        format="json",
    )
    assert resp.status_code == 400
    assert "classe_id" in resp.json()
    assert not Evento.objects.do_dono(perfil).exists()


def test_criar_tarefa_com_classe_do_outro_da_400(api, perfil, outro_perfil):
    classe_alheia = ClasseFactory(dono=outro_perfil, nome="Alheia")
    resp = api.post(
        "/api/v1/tarefas/",
        {"titulo": "minha", "classe_id": str(classe_alheia.id)},
        format="json",
    )
    assert resp.status_code == 400
    assert not Tarefa.objects.do_dono(perfil).exists()


def test_promover_com_classe_do_outro_da_400(api, perfil, outro_perfil):
    tarefa = TarefaFactory(dono=perfil, classe=None)
    classe_alheia = ClasseFactory(dono=outro_perfil, nome="Alheia")
    resp = api.post(
        f"/api/v1/tarefas/{tarefa.id}/promover/",
        {"inicio": "2026-06-01T08:00:00-03:00", "classe_id": str(classe_alheia.id)},
        format="json",
    )
    assert resp.status_code == 400
    assert not Evento.objects.do_dono(perfil).exists()


def test_aplicar_sessoes_de_tarefa_do_outro_e_invalido(perfil, outro_perfil):
    alheia = TarefaFactory(dono=outro_perfil, esforco_estimado=60)
    with pytest.raises(aplicacao.AplicacaoInvalida) as e:
        aplicacao.aplicar_sessoes(
            perfil,
            [
                {
                    "tarefa_id": str(alheia.id),
                    "inicio": aware(2026, 6, 1, 8),
                    "fim": aware(2026, 6, 1, 9),
                }
            ],
        )
    # Tratada como inexistente, não como "de outro dono".
    assert "inexistente" in str(e.value.erros)
    assert not Evento.objects.do_dono(perfil).exists()


# --------------------------------------------------------------------------- #
# 4. Jobs assíncronos — job_id não é credencial                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rota",
    [
        "/api/v1/planejamento/planejar-ia/{job}",
        "/api/v1/planejamento/cenarios/{job}",
        "/api/v1/planejamento/cenarios/refinar/{job}",
        "/api/v1/planejamento/agente/chat/{job}",
    ],
)
def test_job_do_outro_da_404_mesmo_com_resultado_pronto(
    api, perfil, outro_perfil, rota
):
    """Antes do PR1 bastava ter o `job_id` — que carrega o plano inteiro, com
    títulos de tarefas e a agenda. O UUID difícil de adivinhar era obscuridade,
    não fronteira."""
    job = "job-do-outro"
    views._registrar_dono_job(job, outro_perfil)
    for chave in ("cenarios_job", "cenarios_refino", "agente_chat"):
        cache.set(f"{chave}:{job}", {"segredo": "agenda do outro"}, 60)

    resp = api.get(rota.format(job=job))
    assert resp.status_code == 404
    assert "segredo" not in resp.content.decode()


def test_escolher_e_refinar_recusam_lote_do_outro(api, perfil, outro_perfil):
    job = "lote-do-outro"
    views._registrar_dono_job(job, outro_perfil)
    cache.set(f"cenarios_job:{job}", {"cenarios": [{"id": "base"}]}, 60)

    escolher = api.post(
        "/api/v1/planejamento/cenarios/escolher",
        {"job_id": job, "cenario_id": "base"},
        format="json",
    )
    refinar = api.post(
        "/api/v1/planejamento/cenarios/refinar",
        {"job_id": job, "mensagem": "muda aí"},
        format="json",
    )
    assert escolher.status_code == 404
    assert refinar.status_code == 404


def test_chave_de_cache_separa_perfis(perfil, outro_perfil):
    """Mesmas tarefas e mesmo plano em dois perfis não podem colidir no cache —
    senão um recebe o resultado calculado para o outro."""
    args = (["t1"], {"janela_inicio": "08:00"}, [])
    assert tasks._chave_cache(perfil.id, *args) != tasks._chave_cache(
        outro_perfil.id, *args
    )


def test_memoria_da_conversa_do_agente_e_por_perfil(perfil, outro_perfil):
    """`conversa_id` vem do front: dois perfis podem mandar o mesmo."""
    cache.set(
        f"agente_conversa:{outro_perfil.id}:c1", [{"role": "user", "content": "x"}]
    )
    assert cache.get(f"agente_conversa:{perfil.id}:c1") is None


# --------------------------------------------------------------------------- #
# 5. Solver e aprendizado: dados diferentes, resultados diferentes             #
# --------------------------------------------------------------------------- #
def test_agenda_do_outro_nao_ocupa_o_meu_solver(perfil, outro_perfil):
    """Se o "ocupado" não fosse escopado, o solver trataria a agenda de todos
    como bloqueada: plano errado E a agenda alheia inferível pelos buracos."""
    EventoFactory(
        dono=outro_perfil, inicio=aware(2026, 6, 1, 8), fim=aware(2026, 6, 1, 18)
    )
    ocupado = planejamento.intervalos_ocupados(perfil, SEG, aware(2026, 6, 15))
    assert ocupado == []

    tarefa = TarefaFactory(
        dono=perfil, esforco_estimado=120, deadline=aware(2026, 6, 2, 18)
    )
    res = planejamento.montar_plano(perfil, [tarefa], SEG, {})
    assert res.sessoes
    assert res.sessoes[0].inicio == SEG  # o dia inteiro estava livre para mim


def test_validar_tarefas_trata_tarefa_do_outro_como_inexistente(perfil, outro_perfil):
    alheia = TarefaFactory(
        dono=outro_perfil, esforco_estimado=60, deadline=aware(2026, 6, 5, 18)
    )
    validas, invalidas = planejamento.validar_tarefas(perfil, [str(alheia.id)])
    assert validas == []
    assert invalidas == [{"tarefa_id": str(alheia.id), "motivo": "tarefa inexistente"}]


def test_pesos_aprendidos_nao_atravessam_perfis(perfil, outro_perfil):
    """A unicidade global de `metrica` era o pior dos três casos: o 1º perfil a
    gravar um peso travava o aprendizado de todos os outros."""
    PesoPreferencia.objects.create(dono=outro_perfil, metrica="fds_livres", valor=2.5)
    PesoPreferencia.objects.create(dono=perfil, metrica="fds_livres", valor=0.4)

    assert adaptacao.pesos_atuais(perfil)["fds_livres"] == 0.4
    assert adaptacao.pesos_atuais(outro_perfil)["fds_livres"] == 2.5


def test_fator_de_classe_nao_mistura_historico(perfil, outro_perfil):
    from planner.models import RegistroExecucao

    classe_a = ClasseFactory(dono=perfil, nome="Compartilhada")
    classe_b = ClasseFactory(dono=outro_perfil, nome="Compartilhada")  # mesmo nome, ok
    for _ in range(10):
        RegistroExecucao.objects.create(
            dono=outro_perfil, classe=classe_b, planejado_min=60, real_min=180
        )
    assert adaptacao.fator_classe(perfil, str(classe_a.id)) == 1.0
    assert adaptacao.fator_classe(outro_perfil, str(classe_b.id)) > 1.5


def test_classes_de_mesmo_nome_convivem_em_perfis_diferentes(perfil, outro_perfil):
    """Era `Classe.nome unique=True`: o 2º usuário não conseguia ter "Estudar"."""
    a = ClasseFactory(dono=perfil, nome="Estudar 2")
    b = ClasseFactory(dono=outro_perfil, nome="Estudar 2")
    assert a.id != b.id


def test_feriado_municipal_e_por_perfil(perfil, outro_perfil, monkeypatch):
    from datetime import date

    from planner.models import FeriadoLocal

    monkeypatch.setattr(
        holidays, "_nacionais", lambda ano: set()
    )  # isola a camada municipal
    monkeypatch.setattr(holidays, "_estaduais", lambda ano: set())
    FeriadoLocal.objects.create(dono=outro_perfil, nome="Só dele", dia=3, mes=3)

    so_dele = date(2026, 3, 3)
    meus = holidays.feriados_do_ano(2026, perfil)
    # Igualdade exata seria errada: o perfil local herdou o feriado de Curitiba
    # da migration 0006 no backfill. O que importa é a direção do vazamento.
    assert so_dele not in meus
    assert so_dele in holidays.feriados_do_ano(2026, outro_perfil)


# --------------------------------------------------------------------------- #
# 6. Replanejar — o `.delete()` mais perigoso do backend                       #
# --------------------------------------------------------------------------- #
def test_replanejar_nao_apaga_sessoes_do_outro(perfil, outro_perfil):
    alheia = TarefaFactory(
        dono=outro_perfil,
        esforco_estimado=120,
        deadline=aware(2026, 6, 5, 18),
        status=Tarefa.Status.PROMOVIDA,
    )
    sessao_alheia = EventoFactory(
        dono=outro_perfil,
        classe=alheia.classe,
        inicio=aware(2026, 6, 3, 8),
        fim=aware(2026, 6, 3, 10),
        origem_tarefa=alheia,
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )
    minha = TarefaFactory(
        dono=perfil,
        esforco_estimado=120,
        deadline=aware(2026, 6, 5, 18),
        status=Tarefa.Status.PROMOVIDA,
    )
    EventoFactory(
        dono=perfil,
        classe=minha.classe,
        inicio=aware(2026, 6, 4, 8),
        fim=aware(2026, 6, 4, 10),
        origem_tarefa=minha,
        rastrear_conclusao=True,
        status=Evento.Status.AGENDADO,
    )

    rp, criados, removidos = replanejamento.aplicar_replanejamento(perfil, agora=SEG)

    assert Evento.objects.do_dono(outro_perfil).filter(id=sessao_alheia.id).exists()
    assert removidos == 1  # só a minha


# --------------------------------------------------------------------------- #
# 7. Escritas derivadas herdam o dono certo                                    #
# --------------------------------------------------------------------------- #
def test_concluir_grava_registro_no_dono_do_evento(perfil, outro_perfil):
    from planner.models import RegistroExecucao

    evento = EventoFactory(dono=outro_perfil, rastrear_conclusao=True)
    completion.concluir(evento)

    assert RegistroExecucao.objects.do_dono(outro_perfil).count() == 1
    assert RegistroExecucao.objects.do_dono(perfil).count() == 0


def test_remarcar_recria_tarefa_no_dono_do_evento(perfil, outro_perfil):
    evento = EventoFactory(
        dono=outro_perfil, rastrear_conclusao=True, origem_tarefa=None
    )
    _, tarefa = completion.remarcar(evento)
    assert tarefa.dono_id == outro_perfil.id


def test_promover_cria_evento_no_dono_da_tarefa(perfil, outro_perfil):
    from planner.services import tarefas as svc_tarefas

    alheia = TarefaFactory(dono=outro_perfil, esforco_estimado=60)
    evento = svc_tarefas.promover(alheia, inicio=aware(2026, 6, 1, 8))
    assert evento.dono_id == outro_perfil.id


def test_agente_so_enxerga_o_perfil_da_conversa(perfil, outro_perfil):
    """As ferramentas recebem o `dono` por fora dos argumentos do modelo."""
    from planner.services import agente

    TarefaFactory(dono=outro_perfil, titulo="Segredo do outro")
    EventoFactory(dono=outro_perfil, titulo="Evento do outro")

    classes = agente._listar_classes(perfil)
    assert not any("outro" in c["nome"].lower() for c in classes)

    dias = agente._consultar_agenda(perfil, "2026-06-01", "2026-06-02")
    assert dias == []


def test_eventos_na_janela_e_pendentes_sao_escopados(perfil, outro_perfil):
    regra = RegraRecorrenciaFactory(dono=outro_perfil, tipo="SEMANAL", dias=[0])
    EventoFactory(
        dono=outro_perfil,
        inicio=aware(2026, 6, 1, 8),
        fim=aware(2026, 6, 1, 10),
        regra_recorrencia=regra,
    )
    itens = agenda.eventos_na_janela(perfil, aware(2026, 6, 1), aware(2026, 6, 28))
    assert itens == []
    assert list(agenda.pendentes(perfil, timezone.now())) == []
