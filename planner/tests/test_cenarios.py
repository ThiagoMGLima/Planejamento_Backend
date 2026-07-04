"""Testes do pipeline de cenários (Marco C1b) — Ollama sempre mockado.

Cobre: arquétipos derivados do contexto, métricas por código, normalização,
filtro de dominância, pontuação com diversidade, aprendizado de pesos (EWMA)
e os endpoints (202→polling→pronto, degradação sem IA, escolher/aplicar).
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework.test import APIClient

from planner.models import EscolhaCenario, PesoPreferencia
from planner.services import adaptacao
from planner.services import cenarios as C
from planner.services import planejamento as P
from planner.services.planejamento_ia import OllamaIndisponivel

from .factories import TarefaFactory, aware

SEG = aware(2026, 6, 1, 8)


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cenarios-tests",
        }
    }
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def eager():
    from config.celery import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    app.conf.task_store_eager_result = True
    yield
    app.conf.task_always_eager = False
    app.conf.task_store_eager_result = False


# --------------------------------------------------------------------------- #
# Arquétipos (derivam dos FATOS, nada fixo)                                    #
# --------------------------------------------------------------------------- #
_CTX = {
    "agora": "2026-06-01T08:00:00-03:00",
    "horizonte_fim": "2026-06-14T18:00:00-03:00",
    "preferencias": {"janela_inicio": "08:00", "janela_fim": "22:00"},
    "carga_resumo": {"carga_media_dia_min": 90},
    "tarefas": [
        {"id": "A", "deadline": "2026-06-03T18:00:00-03:00"},
        {"id": "B", "deadline": "2026-06-05T18:00:00-03:00"},
        {"id": "C", "deadline": "2026-06-10T18:00:00-03:00"},
        {"id": "D", "deadline": "2026-06-13T18:00:00-03:00"},
    ],
}


def test_arquetipo_espalhado_usa_carga_media_real():
    assert C.ARQUETIPOS["espalhado"](_CTX) == {"max_min_por_dia_total": 90}
    vazio = {**_CTX, "carga_resumo": {"carga_media_dia_min": 0}}
    assert C.ARQUETIPOS["espalhado"](vazio) == {}


def test_arquetipo_intenso_estende_janela_e_bloqueia_fds():
    d = C.ARQUETIPOS["intenso"](_CTX)
    # 22:00 + 2h estoura o dia → clamp no teto do guarda-corpo (23:59).
    assert d["janela_por_dia"] == {str(i): ["08:00", "23:59"] for i in range(5)}
    assert d["dias_bloqueados"] == [
        "2026-06-06",
        "2026-06-07",
        "2026-06-13",
        "2026-06-14",
    ]


def test_arquetipo_frente_carregada_pega_as_3_deadlines_mais_proximas():
    d = C.ARQUETIPOS["frente_carregada"](_CTX)
    assert d == {
        "ajustes_por_tarefa": {
            "A": {"buffer_dias": 2},
            "B": {"buffer_dias": 2},
            "C": {"buffer_dias": 2},
        }
    }


# --------------------------------------------------------------------------- #
# Métricas e normalização                                                      #
# --------------------------------------------------------------------------- #
def _resultado(tarefas, agora=SEG, horizonte=None, prefs_entrada=None):
    prefs, usadas = P.montar_preferencias(prefs_entrada or {})
    horizonte = horizonte or max(t.deadline for t in tarefas)
    sessoes, nao = P.calcular_plano(tarefas, [], prefs, agora, horizonte)
    return P.ResultadoPlano(sessoes, nao, prefs, usadas, tarefas, [], agora, horizonte)


def test_metricas_do_plano_valores_grounded():
    t = P.TarefaEntrada("A", "A", "c1", 240, aware(2026, 6, 5, 18))
    m = C.metricas_do_plano(_resultado([t]))
    # 120/dia → seg 08–10 e ter 08–10; qua/qui/sex livres; sem fds no horizonte.
    assert m["pico_min_dia"] == 120
    assert m["dias_livres"] == 3
    assert m["fds_livres"] == 0
    assert m["folga_media_h"] == 80.0  # ter 10:00 → sex 18:00
    assert m["min_fora_janela"] == 0
    assert m["fragmentacao"] == 2.0
    assert m["nao_alocado_min"] == 0


def test_metricas_min_fora_janela_conta_fds_e_madrugada():
    # Sábado como início e deadline domingo: relaxamento usa o fds inteiro.
    t = P.TarefaEntrada("A", "A", "c1", 120, aware(2026, 6, 7, 22))
    m = C.metricas_do_plano(_resultado([t], agora=aware(2026, 6, 6, 8)))
    assert m["min_fora_janela"] == 120  # todo o trabalho caiu no fds


def test_normalizar_base_zera_e_inverte_custos():
    base = {
        "pico_min_dia": 120,
        "dias_livres": 2,
        "fds_livres": 1,
        "folga_media_h": 10.0,
        "min_fora_janela": 0,
        "fragmentacao": 2.0,
        "nao_alocado_min": 0,
    }
    assert all(v == 0 for v in C.normalizar(base, base).values())
    melhor_pico = {**base, "pico_min_dia": 60}  # custo caiu → normalizado positivo
    assert C.normalizar(melhor_pico, base)["pico_min_dia"] == 0.5
    mais_fds = {**base, "fds_livres": 2}  # benefício subiu → positivo
    assert C.normalizar(mais_fds, base)["fds_livres"] == 1.0


# --------------------------------------------------------------------------- #
# Dominância e pontuação                                                       #
# --------------------------------------------------------------------------- #
def _cenario(cid, nao_alocado=0, plano_chave=None, **vs_base):
    norm = {m: 0.0 for m in C.METRICAS}
    norm.update(vs_base)
    metricas = {m: 0 for m in C.METRICAS}
    metricas["nao_alocado_min"] = nao_alocado
    res = SimpleNamespace(
        sessoes=[
            SimpleNamespace(
                tarefa_id=k, inicio=aware(2026, 6, 1, 8), fim=aware(2026, 6, 1, 9)
            )
            for k in (plano_chave or [cid])
        ]
    )
    return {
        "id": cid,
        "nome": cid,
        "intencao": "",
        "diretrizes": {},
        "res": res,
        "metricas": metricas,
        "metricas_vs_base": norm,
    }


def test_filtrar_dominados_remove_pior_em_tudo():
    base = _cenario("base")
    bom = _cenario("bom", dias_livres=0.5, pico_min_dia=0.2)
    ruim = _cenario("ruim", dias_livres=-0.1, pico_min_dia=-0.3)  # pior que base
    vivos = C.filtrar_dominados([base, bom, ruim])
    assert [c["id"] for c in vivos] == ["base", "bom"]


def test_filtrar_dominados_remove_plano_duplicado():
    base = _cenario("base", plano_chave=["X"])
    copia = _cenario("copia", plano_chave=["X"], dias_livres=0.1)
    vivos = C.filtrar_dominados([base, copia])
    assert [c["id"] for c in vivos] == ["base"]


def test_filtrar_dominados_elimina_quem_aloca_menos_que_o_base():
    base = _cenario("base", nao_alocado=30)
    # Ótimo em tudo, mas deixa mais coisa de fora que o base → morre direto.
    guloso = _cenario("guloso", nao_alocado=60, dias_livres=2.0, fds_livres=2.0)
    vivos = C.filtrar_dominados([base, guloso])
    assert [c["id"] for c in vivos] == ["base"]


def test_filtrar_dominados_mantem_trade_offs_legitimos():
    base = _cenario("base")
    a = _cenario("a", dias_livres=0.5, min_fora_janela=-0.2)  # ganha dia, paga janela
    b = _cenario("b", min_fora_janela=0.1, dias_livres=-0.1)  # o inverso
    assert {c["id"] for c in C.filtrar_dominados([base, a, b])} == {"base", "a", "b"}


def test_pontuar_ordena_sugere_e_garante_diversidade():
    pesos = {m: 1.0 for m in C.METRICAS}
    pesos["fds_livres"] = 0.2  # métrica de menor peso → define o contrariante
    base = _cenario("base")
    a = _cenario("a", pico_min_dia=0.5)
    b = _cenario("b", dias_livres=0.3)
    c = _cenario("c", folga_media_h=0.2)
    d = _cenario("d", fds_livres=1.0)  # forte só na métrica fraca
    e = _cenario("e", min_fora_janela=0.1)
    selecao = C.pontuar([base, a, b, c, d, e], pesos)
    ids = [x["id"] for x in selecao]
    assert len(ids) == C.MAX_CENARIOS
    assert "base" in ids  # base sempre presente
    assert "d" in ids  # contrariante sempre presente
    assert selecao[0]["id"] == "a" and selecao[0]["sugerido"]  # maior score
    assert sum(1 for x in selecao if x["sugerido"]) == 1
    scores = [x["score"] for x in selecao]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------- #
# Aprendizado de pesos (EWMA)                                                  #
# --------------------------------------------------------------------------- #
def _escolha(escolhido, lote):
    return EscolhaCenario.objects.create(
        lote=lote,
        escolhido=escolhido,
        era_sugerido=False,
        pesos_no_momento={m: 1.0 for m in C.METRICAS},
    )


def _item(cid, **vs_base):
    c = _cenario(cid, **vs_base)
    return {k: c[k] for k in ("id", "nome", "metricas", "metricas_vs_base")}


@pytest.mark.django_db
def test_atualizar_pesos_move_na_direcao_da_escolha():
    escolha = _escolha(
        "fds", [_item("fds", fds_livres=1.0, pico_min_dia=-0.5), _item("base")]
    )
    novos = adaptacao.atualizar_pesos(escolha)
    # Escolhido é melhor em fds_livres (+1 vs 0) e pior em pico (−0.5 vs 0).
    assert novos["fds_livres"] == pytest.approx(1.0 + 0.1 * 1.0)
    assert novos["pico_min_dia"] == pytest.approx(1.0 - 0.1 * 0.5)
    assert novos["dias_livres"] == 1.0  # sem diferença → não mexe


@pytest.mark.django_db
def test_atualizar_pesos_respeita_clamp():
    PesoPreferencia.objects.create(metrica="fds_livres", valor=2.95)
    PesoPreferencia.objects.create(metrica="pico_min_dia", valor=0.22)
    escolha = _escolha(
        "x", [_item("x", fds_livres=2.0, pico_min_dia=-2.0), _item("base")]
    )
    novos = adaptacao.atualizar_pesos(escolha)
    assert novos["fds_livres"] == adaptacao.PESO_MAX
    assert novos["pico_min_dia"] == adaptacao.PESO_MIN


@pytest.mark.django_db
def test_atualizar_pesos_lote_sem_rejeitados_nao_ensina():
    escolha = _escolha("unico", [_item("unico", fds_livres=1.0)])
    assert adaptacao.atualizar_pesos(escolha) == adaptacao.pesos_atuais()
    assert PesoPreferencia.objects.count() == 0


# --------------------------------------------------------------------------- #
# Endpoints (Celery eager; IA mockada)                                         #
# --------------------------------------------------------------------------- #
def _tarefas_validas():
    """Carga desigual (pico na segunda): garante que arquétipos como o
    'espalhado' (teto = carga média < pico) produzam plano ≠ base — sem isso
    tudo colapsa no base via dedup e o lote teria um cenário só."""
    return [
        TarefaFactory(esforco_estimado=240, deadline=aware(2026, 6, 2, 18)),
        TarefaFactory(esforco_estimado=120, deadline=aware(2026, 6, 5, 18)),
    ]


def _post_cenarios(api, tarefas, **extra):
    body = {
        "tarefa_ids": [str(t.id) for t in tarefas],
        "a_partir_de": SEG.isoformat(),
        **extra,
    }
    return api.post("/api/v1/planejamento/cenarios", body, format="json")


_CANDIDATOS_IA = [
    {
        "nome": "Sábado livre",
        "intencao": "Estender a quinta e ganhar o sábado.",
        "diretrizes": {
            "janela_por_dia": {"3": ["08:00", "20:00"]},
            "dias_bloqueados": ["2026-06-06"],
        },
    },
    {
        "nome": "Ritmo leve",
        "intencao": "Menos carga por dia.",
        "diretrizes": {"max_min_por_dia_total": 90},
    },
    {
        "nome": "Prioridade total",
        "intencao": "Focar na tarefa mais urgente.",
        "diretrizes": {"prioridades": {}},
    },
]


@pytest.mark.django_db
def test_fluxo_202_polling_pronto(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = True
    tarefas = _tarefas_validas()
    with mock.patch(
        "planner.services.cenarios.gerar_cenarios_ia", return_value=_CANDIDATOS_IA
    ):
        resp = _post_cenarios(api, tarefas)
    assert resp.status_code == 202
    job_id = resp.data["job_id"]
    assert resp.data["tempo_estimado_s"] > 0

    status = api.get(f"/api/v1/planejamento/cenarios/{job_id}")
    assert status.data["status"] == "pronto"
    resultado = status.data["resultado"]
    assert resultado["ia_indisponivel"] is False
    ids = [c["id"] for c in resultado["cenarios"]]
    assert "base" in ids
    assert len(ids) <= C.MAX_CENARIOS
    assert sum(1 for c in resultado["cenarios"] if c["sugerido"]) == 1
    for c in resultado["cenarios"]:
        assert set(c["metricas"]) == set(C.METRICAS)
        assert "sessoes" in c["plano"]
        assert isinstance(c["trade_offs"], list)


@pytest.mark.django_db
def test_ollama_fora_degrada_para_arquetipos(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = True
    tarefas = _tarefas_validas()
    with mock.patch(
        "planner.services.cenarios.gerar_cenarios_ia",
        side_effect=OllamaIndisponivel("down"),
    ):
        resp = _post_cenarios(api, tarefas)
    status = api.get(f"/api/v1/planejamento/cenarios/{resp.data['job_id']}")
    resultado = status.data["resultado"]
    assert resultado["ia_indisponivel"] is True
    arquetipos = {"base", "espalhado", "intenso", "frente-carregada"}
    assert {c["id"] for c in resultado["cenarios"]} <= arquetipos
    assert "base" in {c["id"] for c in resultado["cenarios"]}


@pytest.mark.django_db
def test_ia_ruim_nao_quebra_o_pipeline(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = True
    tarefas = _tarefas_validas()
    lixo = [
        {
            "nome": "Absurdo",
            "intencao": "x",
            "diretrizes": {
                "prioridades": {"id-inexistente": 99},
                "janela_por_dia": {"9": ["02:00", "01:00"]},
                "dias_bloqueados": ["3000-01-01", "lixo"],
                "usar_fds": "sim",
            },
        },
        "nem-dict",
    ]
    with mock.patch("planner.services.cenarios.gerar_cenarios_ia", return_value=lixo):
        resp = _post_cenarios(api, tarefas)
    status = api.get(f"/api/v1/planejamento/cenarios/{resp.data['job_id']}")
    assert status.data["status"] == "pronto"
    # O candidato lixo vira diretrizes vazias → plano idêntico ao base → dedup.
    assert "absurdo" not in {c["id"] for c in status.data["resultado"]["cenarios"]}


@pytest.mark.django_db
def test_cache_hit_devolve_200_pronto(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False  # sem IA: determinístico e rápido
    tarefas = _tarefas_validas()
    _post_cenarios(api, tarefas)  # popula o cache
    resp = _post_cenarios(api, tarefas)
    assert resp.status_code == 200
    assert resp.data["status"] == "pronto"


@pytest.mark.django_db
def test_tarefa_invalida_422(api):
    resp = _post_cenarios(
        api, [SimpleNamespace(id="00000000-0000-0000-0000-000000000000")]
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_escolher_grava_escolha_e_atualiza_pesos(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    resp = _post_cenarios(api, tarefas)
    job_id = resp.data["job_id"]
    resultado = api.get(f"/api/v1/planejamento/cenarios/{job_id}").data["resultado"]
    alvo = next(c for c in resultado["cenarios"] if c["id"] != "base")

    resp = api.post(
        "/api/v1/planejamento/cenarios/escolher",
        {"job_id": job_id, "cenario_id": alvo["id"], "aplicar": False},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["aplicado"] is False
    escolha = EscolhaCenario.objects.get()
    assert escolha.escolhido == alvo["id"]
    assert escolha.era_sugerido == alvo["sugerido"]
    assert {c["id"] for c in escolha.lote} == {c["id"] for c in resultado["cenarios"]}
    assert escolha.pesos_no_momento == resultado["pesos_usados"]
    assert PesoPreferencia.objects.count() == len(C.METRICAS)


@pytest.mark.django_db
def test_escolher_com_aplicar_persiste_o_plano(api, eager, settings):
    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    resp = _post_cenarios(api, tarefas)
    job_id = resp.data["job_id"]
    resultado = api.get(f"/api/v1/planejamento/cenarios/{job_id}").data["resultado"]

    resp = api.post(
        "/api/v1/planejamento/cenarios/escolher",
        {"job_id": job_id, "cenario_id": "base", "aplicar": True},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["aplicado"] is True
    base = next(c for c in resultado["cenarios"] if c["id"] == "base")
    assert resp.data["eventos_criados"] == len(base["plano"]["sessoes"])
    for t in tarefas:
        t.refresh_from_db()
        assert t.status == "PROMOVIDA"


@pytest.mark.django_db
def test_escolher_job_desconhecido_404_e_cenario_errado_400(api, eager, settings):
    resp = api.post(
        "/api/v1/planejamento/cenarios/escolher",
        {"job_id": "nao-existe", "cenario_id": "base"},
        format="json",
    )
    assert resp.status_code == 404

    settings.IA_PLANEJAMENTO_ENABLED = False
    tarefas = _tarefas_validas()
    job_id = _post_cenarios(api, tarefas).data["job_id"]
    api.get(f"/api/v1/planejamento/cenarios/{job_id}")
    resp = api.post(
        "/api/v1/planejamento/cenarios/escolher",
        {"job_id": job_id, "cenario_id": "nao-existe"},
        format="json",
    )
    assert resp.status_code == 400
    assert EscolhaCenario.objects.count() == 0
