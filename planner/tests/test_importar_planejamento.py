"""Testes do `importar_planejamento_ensino` (Fase 1.2 / PR B).

O que este comando pode fazer de pior não é levantar exceção — é gravar uma
`Ocorrencia` numa data que a série não gera. Ela fica no banco e **nunca**
aparece no calendário, sem erro e sem log (§2.5 do plano). Vários testes daqui
existem só para provar que esse caso é recusado.
"""

import json
from datetime import date, time

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from planner.models import Classe, Evento, Ocorrencia
from planner.services import perfis
from planner.services.recurrence import expandir
from planner.tests.factories import ClasseFactory, aware

# Segunda-feira 2026-08-17; o semestre de teste vai até 2026-09-14.
SPEC_BASE = {
    "disciplina": {
        "codigo": "TST01",
        "nome": "Disciplina de Teste",
        "sigla": "TST",
        "classe": "Aula",
        "rastrear_conclusao": False,
    },
    "semestre": "2026-2",
    "data_inicio": "2026-08-17",
    "data_fim": "2026-09-28",
    "recorrencias": [{"dias": [0], "inicio": "15:50", "fim": "17:30"}],
    "datas": [
        {"data": "2026-08-17", "conteudo": "Apresentação"},
        {"data": "2026-08-24", "conteudo": "Sinais e sistemas"},
        {"data": "2026-08-31", "conteudo": "Revisão", "prova": "Prova 1"},
        {"data": "2026-09-21", "sem_aula": "Semana de recesso"},
        {"data": "2026-09-14", "conteudo": "Laboratório", "entrega": "Entrega P1"},
        {"data": "2026-09-28", "conteudo": "Fechamento"},
    ],
}


@pytest.fixture
def classes(perfil):
    """As classes que o importador resolve pelo nome, no escopo do dono.

    As 5 padrão já existem por migration/seed no perfil local — a fixture as
    BUSCA, não cria, senão esbarra em `uq_classe_dono_nome`.
    """
    return {
        nome: _classe(perfil, nome, cor)
        for nome, cor in (("Aula", "#e8f0fe"), ("Prova", "#fbeaea"))
    }


def _classe(dono, nome, cor):
    existente = Classe.objects.do_dono(dono).filter(nome=nome).first()
    return existente or ClasseFactory(dono=dono, nome=nome, cor=cor)


@pytest.fixture
def escrever(tmp_path):
    def _escrever(spec):
        p = tmp_path / "disciplina.json"
        p.write_text(json.dumps(spec), encoding="utf-8")
        return str(p)

    return _escrever


def importar(caminho, **kwargs):
    call_command("importar_planejamento_ensino", caminho, **kwargs)


def datas_visiveis(evento):
    """As datas que o calendário REALMENTE mostra para esta série."""
    return [
        v.data for v in expandir(evento, aware(2026, 8, 1), aware(2026, 10, 1), set())
    ]


# --------------------------------------------------------------------------- #
# A série                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_cria_serie_no_formato_das_que_ja_existem(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    ev = Evento.objects.do_dono(perfil).get()
    assert ev.titulo == "Disciplina de Teste (TST01)"
    assert ev.classe == classes["Aula"]
    assert ev.rastrear_conclusao is False
    assert ev.regra_recorrencia.tipo == "SEMANAL"
    assert ev.regra_recorrencia.dias == [0]
    assert ev.regra_recorrencia.data_fim == date(2026, 9, 28)


@pytest.mark.django_db
def test_horario_do_json_e_local_nao_utc(perfil, classes, escrever):
    """O JSON traz a hora da grade; o banco guarda UTC.

    Sem a conversão, `15:50` entraria como 15:50 UTC e a aula apareceria às
    12:50 para o usuário — deslocada em 3 horas, em silêncio.
    """
    importar(escrever(SPEC_BASE), aplicar=True)

    ev = Evento.objects.do_dono(perfil).get()
    local = timezone.localtime(ev.inicio)
    assert local.time() == time(15, 50)
    assert timezone.localtime(ev.fim).time() == time(17, 30)


@pytest.mark.django_db
def test_serie_ignora_feriados_por_padrao(perfil, classes, escrever):
    """O default é o mesmo das séries lançadas à mão: feriado não tem aula.

    Quem quiser o contrário declara `ignorar_feriados: false` no JSON — é
    escolha da disciplina, não deste comando.
    """
    importar(escrever(SPEC_BASE), aplicar=True)

    ev = Evento.objects.do_dono(perfil).get()
    assert ev.regra_recorrencia.ignorar_feriados is True


@pytest.mark.django_db
def test_duas_recorrencias_viram_dois_eventos(perfil, classes, escrever):
    """O caso ASL: mesma hora, durações diferentes, não cabe numa regra só."""
    spec = {
        **SPEC_BASE,
        "recorrencias": [
            {"dias": [0], "inicio": "15:50", "fim": "17:30"},
            {"dias": [3], "inicio": "15:50", "fim": "18:40"},
        ],
        "datas": [
            {"data": "2026-08-17", "conteudo": "Segunda"},
            {"data": "2026-08-20", "conteudo": "Quinta"},
        ],
    }
    importar(escrever(spec), aplicar=True)

    eventos = Evento.objects.do_dono(perfil).order_by("fim")
    assert eventos.count() == 2
    seg, qui = eventos
    assert seg.regra_recorrencia.dias == [0]
    assert qui.regra_recorrencia.dias == [3]
    # A duração é o que uma regra sozinha não expressaria.
    assert (seg.fim - seg.inicio) != (qui.fim - qui.inicio)

    # Cada data foi para o evento do dia certo.
    assert Ocorrencia.objects.get(data=date(2026, 8, 17)).evento == seg
    assert Ocorrencia.objects.get(data=date(2026, 8, 20)).evento == qui


# --------------------------------------------------------------------------- #
# Conteúdo por data                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_conteudo_vai_para_descricao_e_titulo_fica_da_serie(perfil, classes, escrever):
    """D1: semana comum mantém o nome fixo da disciplina no bloco."""
    importar(escrever(SPEC_BASE), aplicar=True)

    oc = Ocorrencia.objects.get(data=date(2026, 8, 24))
    assert oc.descricao_override == "Sinais e sistemas"
    assert oc.titulo_override == ""
    assert oc.classe_override is None


@pytest.mark.django_db
def test_prova_troca_titulo_e_classe(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    oc = Ocorrencia.objects.get(data=date(2026, 8, 31))
    assert oc.titulo_override == "Prova 1"
    assert oc.classe_override == classes["Prova"]
    assert oc.descricao_override == "Revisão"


@pytest.mark.django_db
def test_entrega_troca_titulo_mas_nao_a_classe(perfil, classes, escrever):
    """D2: se entrega e prova tiverem a mesma cor, vermelho perde o sentido."""
    importar(escrever(SPEC_BASE), aplicar=True)

    oc = Ocorrencia.objects.get(data=date(2026, 9, 14))
    assert oc.titulo_override == "Entrega P1"
    assert oc.classe_override is None


@pytest.mark.django_db
def test_sem_aula_vira_pulado_e_some_do_calendario(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    oc = Ocorrencia.objects.get(data=date(2026, 9, 21))
    assert oc.status_override == "PULADO"
    assert oc.descricao_override == "Semana de recesso"

    ev = Evento.objects.do_dono(perfil).get()
    assert date(2026, 9, 21) not in datas_visiveis(ev)


# --------------------------------------------------------------------------- #
# A autoridade do JSON (gate Q2)                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_data_sem_conteudo_continua_sendo_aula(perfil, classes, escrever):
    """Quem manda em QUANDO há aula é a série, não o arquivo.

    Uma data que a regra gera e o JSON não lista significa quase sempre
    transcrição incompleta — e some do relatório como aviso, não do calendário.
    """
    spec = {**SPEC_BASE, "datas": [{"data": "2026-08-17", "conteudo": "Só esta"}]}
    importar(escrever(spec), aplicar=True)

    ev = Evento.objects.do_dono(perfil).get()
    # 6 segundas entre 17/08 e 28/09, todas ainda no calendário.
    assert len(datas_visiveis(ev)) == 7
    assert not Ocorrencia.objects.filter(status_override="PULADO").exists()


@pytest.mark.django_db
def test_relata_os_dias_sem_conteudo(perfil, classes, escrever, capsys):
    spec = {**SPEC_BASE, "datas": [{"data": "2026-08-17", "conteudo": "Só esta"}]}
    importar(escrever(spec), aplicar=True)

    saida = capsys.readouterr().out
    assert "sem conteúdo transcrito" in saida
    assert "24/08" in saida


@pytest.mark.django_db
def test_aula_em_feriado_e_recusada_por_padrao(perfil, classes, escrever):
    """07/09 é feriado e o default é não ter aula em feriado.

    Gravar a ocorrência ali seria escrevê-la onde a série não passa: nunca
    apareceria. O erro diz as duas saídas possíveis.
    """
    spec = {
        **SPEC_BASE,
        "datas": [{"data": "2026-09-07", "conteudo": "Aula no feriado"}],
    }
    with pytest.raises(CommandError, match="é feriado"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_disciplina_pode_declarar_que_tem_aula_em_feriado(perfil, classes, escrever):
    """`ignorar_feriados: false` é a escolha DA SÉRIE, e o comando a respeita."""
    spec = {
        **SPEC_BASE,
        "ignorar_feriados": False,
        "datas": [{"data": "2026-09-07", "conteudo": "Aula no feriado"}],
    }
    importar(escrever(spec), aplicar=True)

    ev = Evento.objects.do_dono(perfil).get()
    assert ev.regra_recorrencia.ignorar_feriados is False
    assert date(2026, 9, 7) in datas_visiveis(ev)


# --------------------------------------------------------------------------- #
# Datas que a série não gera — o modo de falha silencioso                     #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_data_em_dia_da_semana_errado_e_recusada(perfil, classes, escrever):
    spec = {**SPEC_BASE, "datas": [{"data": "2026-08-18", "conteudo": "Terça"}]}

    with pytest.raises(CommandError, match="não é dia de aula"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_data_alem_do_fim_do_semestre_e_recusada(perfil, classes, escrever):
    spec = {**SPEC_BASE, "datas": [{"data": "2026-10-05", "conteudo": "Tarde demais"}]}

    with pytest.raises(CommandError, match="não é dia de aula"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_recusa_nao_deixa_nada_gravado(perfil, classes, escrever):
    """A recusa é atômica: metade importada seria pior que nada."""
    spec = {
        **SPEC_BASE,
        "datas": [
            {"data": "2026-08-17", "conteudo": "Válida"},
            {"data": "2026-08-18", "conteudo": "Terça — inválida"},
        ],
    }
    with pytest.raises(CommandError):
        importar(escrever(spec), aplicar=True)

    assert not Evento.objects.do_dono(perfil).exists()
    assert not Ocorrencia.objects.exists()


# --------------------------------------------------------------------------- #
# Idempotência                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_rodar_duas_vezes_nao_duplica(perfil, classes, escrever):
    caminho = escrever(SPEC_BASE)
    importar(caminho, aplicar=True)
    importar(caminho, aplicar=True)

    assert Evento.objects.do_dono(perfil).count() == 1
    assert Ocorrencia.objects.count() == 6


@pytest.mark.django_db
def test_segunda_importacao_nao_rejeita_o_proprio_resultado(perfil, classes, escrever):
    """Regressão: o importador não pode ler o próprio PULADO como "não é aula".

    A 1ª execução marca 07/09 como PULADO (`sem_aula` no JSON). Se a validação
    de datas perguntasse a `expandir` — que aplica overrides — 07/09 sumiria do
    conjunto de dias de aula e a 2ª execução recusaria o mesmo arquivo que a 1ª
    aceitou. A pergunta certa é o que a REGRA gera, e é `datas_da_regra`.
    """
    caminho = escrever(SPEC_BASE)
    importar(caminho, aplicar=True)
    importar(caminho, aplicar=True)  # não pode levantar

    oc = Ocorrencia.objects.get(data=date(2026, 9, 21))
    assert oc.status_override == "PULADO"


@pytest.mark.django_db
def test_renomear_a_disciplina_nao_cria_segunda_serie(perfil, classes, escrever):
    """A razão de `chave_importacao` existir (gate Q1).

    Casando por título, renomear pela UI faria a próxima importação criar uma
    SEGUNDA série — duas aulas no mesmo horário, sem ninguém perceber.
    """
    importar(escrever(SPEC_BASE), aplicar=True)
    ev = Evento.objects.do_dono(perfil).get()
    ev.titulo = "Nome que o usuário preferiu"
    ev.save()

    importar(escrever(SPEC_BASE), aplicar=True)

    assert Evento.objects.do_dono(perfil).count() == 1
    assert Evento.objects.do_dono(perfil).get().titulo == "Disciplina de Teste (TST01)"


@pytest.mark.django_db
def test_corrigir_uma_linha_e_re_rodar_atualiza_no_lugar(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    spec = {
        **SPEC_BASE,
        "datas": [
            {**d, "conteudo": "Conteúdo corrigido"} if d["data"] == "2026-08-24" else d
            for d in SPEC_BASE["datas"]
        ],
    }
    importar(escrever(spec), aplicar=True)

    oc = Ocorrencia.objects.get(data=date(2026, 8, 24))
    assert oc.descricao_override == "Conteúdo corrigido"
    assert Ocorrencia.objects.count() == 6


@pytest.mark.django_db
def test_tirar_a_prova_do_json_devolve_o_dia_para_aula(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    spec = {
        **SPEC_BASE,
        "datas": [
            (
                {"data": d["data"], "conteudo": d.get("conteudo", "")}
                if d["data"] == "2026-08-31"
                else d
            )
            for d in SPEC_BASE["datas"]
        ],
    }
    importar(escrever(spec), aplicar=True)

    oc = Ocorrencia.objects.get(data=date(2026, 8, 31))
    assert oc.classe_override is None
    assert oc.titulo_override == ""


@pytest.mark.django_db
def test_encolher_o_semestre_remove_as_ocorrencias_orfas(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    spec = {
        **SPEC_BASE,
        "data_fim": "2026-08-24",
        "datas": [d for d in SPEC_BASE["datas"] if d["data"] <= "2026-08-24"],
    }
    importar(escrever(spec), aplicar=True)

    assert Ocorrencia.objects.count() == 2


# --------------------------------------------------------------------------- #
# Estado do usuário é preservado                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_data_concluida_pelo_usuario_nao_e_pulada(perfil, classes, escrever):
    """Ele assistiu a aula; o planejamento não a lista. O comando não desfaz."""
    importar(escrever(SPEC_BASE), aplicar=True)
    oc = Ocorrencia.objects.get(data=date(2026, 8, 24))
    oc.status_override = "CONCLUIDO"
    oc.save()

    spec = {**SPEC_BASE, "datas": [{"data": "2026-08-17", "conteudo": "Só esta"}]}
    importar(escrever(spec), aplicar=True)

    oc.refresh_from_db()
    assert oc.status_override == "CONCLUIDO"


# --------------------------------------------------------------------------- #
# Dry-run                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_sem_aplicar_nada_e_gravado(perfil, classes, escrever):
    importar(escrever(SPEC_BASE))

    assert not Evento.objects.do_dono(perfil).exists()
    assert not Ocorrencia.objects.exists()


@pytest.mark.django_db
def test_dry_run_valida_de_verdade(perfil, classes, escrever):
    """A simulação roda a importação inteira antes de desfazer.

    Se ela não escrevesse, não haveria série para expandir e a validação de
    datas (o cheque que mais importa) só apareceria no `--aplicar`.
    """
    spec = {**SPEC_BASE, "datas": [{"data": "2026-08-18", "conteudo": "Terça"}]}

    with pytest.raises(CommandError, match="não é dia de aula"):
        importar(escrever(spec))


# --------------------------------------------------------------------------- #
# --substituir                                                                #
# --------------------------------------------------------------------------- #
def _avulso(perfil, classe, titulo, dia):
    return Evento.objects.create(
        dono=perfil,
        titulo=titulo,
        classe=classe,
        inicio=aware(2026, 8, dia, 15, 50),
        fim=aware(2026, 8, dia, 17, 30),
        rastrear_conclusao=False,
    )


@pytest.mark.django_db
def test_substituir_apaga_so_o_prefixo_da_disciplina(perfil, classes, escrever):
    meu = _avulso(perfil, classes["Aula"], "TST — Aula lançada à mão", 17)
    de_outra = _avulso(perfil, classes["Aula"], "ASL — Aula de outra disciplina", 17)

    importar(escrever(SPEC_BASE), aplicar=True, substituir=True)

    assert not Evento.objects.do_dono(perfil).filter(pk=meu.pk).exists()
    assert Evento.objects.do_dono(perfil).filter(pk=de_outra.pk).exists()


@pytest.mark.django_db
def test_substituir_nao_toca_em_bloco_de_estudo(perfil, classes, escrever):
    """Um avulso de "Estudar" é bloco promovido, nunca planejamento de ensino."""
    estudar = _classe(perfil, "Estudar", "#d9e8d4")
    bloco = _avulso(perfil, estudar, "TST — Estudar para a prova", 18)

    importar(escrever(SPEC_BASE), aplicar=True, substituir=True)

    assert Evento.objects.do_dono(perfil).filter(pk=bloco.pk).exists()


@pytest.mark.django_db
def test_substituir_recusa_avulso_concluido(perfil, classes, escrever):
    ev = _avulso(perfil, classes["Aula"], "TST — Aula que eu assisti", 17)
    ev.status = Evento.Status.CONCLUIDO
    ev.save()

    with pytest.raises(CommandError, match="estado do usuário"):
        importar(escrever(SPEC_BASE), aplicar=True, substituir=True)

    assert Evento.objects.do_dono(perfil).filter(pk=ev.pk).exists()


@pytest.mark.django_db
def test_sem_substituir_os_avulsos_ficam(perfil, classes, escrever):
    ev = _avulso(perfil, classes["Aula"], "TST — Aula lançada à mão", 17)

    importar(escrever(SPEC_BASE), aplicar=True)

    assert Evento.objects.do_dono(perfil).filter(pk=ev.pk).exists()


# --------------------------------------------------------------------------- #
# Validação do JSON                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_duas_recorrencias_no_mesmo_dia_e_erro(perfil, classes, escrever):
    spec = {
        **SPEC_BASE,
        "recorrencias": [
            {"dias": [0], "inicio": "15:50", "fim": "17:30"},
            {"dias": [0], "inicio": "19:00", "fim": "20:00"},
        ],
    }
    with pytest.raises(CommandError, match="duas recorrências"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_data_repetida_e_erro(perfil, classes, escrever):
    spec = {
        **SPEC_BASE,
        "datas": [
            {"data": "2026-08-17", "conteudo": "A"},
            {"data": "2026-08-17", "conteudo": "B"},
        ],
    }
    with pytest.raises(CommandError, match="duas vezes"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_prova_e_entrega_no_mesmo_dia_e_erro(perfil, classes, escrever):
    spec = {
        **SPEC_BASE,
        "datas": [{"data": "2026-08-17", "prova": "P1", "entrega": "E1"}],
    }
    with pytest.raises(CommandError, match="prova e entrega"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_dia_da_semana_fora_de_0_a_6_e_erro(perfil, classes, escrever):
    spec = {
        **SPEC_BASE,
        "recorrencias": [{"dias": [7], "inicio": "8:00", "fim": "9:00"}],
    }
    with pytest.raises(CommandError, match="0=seg"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_classe_inexistente_e_erro_com_nome(perfil, classes, escrever):
    spec = {
        **SPEC_BASE,
        "disciplina": {**SPEC_BASE["disciplina"], "classe": "Inventada"},
    }
    with pytest.raises(CommandError, match="Inventada"):
        importar(escrever(spec), aplicar=True)


@pytest.mark.django_db
def test_arquivo_inexistente_e_erro(perfil, classes):
    with pytest.raises(CommandError, match="não encontrado"):
        importar("/tmp/nao-existe-planejamento.json")


# --------------------------------------------------------------------------- #
# Isolamento                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_importa_no_dono_certo(perfil, outro_perfil, classes, escrever):
    _classe(outro_perfil, "Aula", "#e8f0fe")
    _classe(outro_perfil, "Prova", "#fbeaea")

    importar(escrever(SPEC_BASE), aplicar=True, dono=outro_perfil.email)

    assert Evento.objects.do_dono(outro_perfil).count() == 1
    assert Evento.objects.do_dono(perfil).count() == 0


@pytest.mark.django_db
def test_nao_usa_classe_de_outro_dono(perfil, outro_perfil, escrever):
    """A classe do JSON é resolvida no escopo do dono, nunca globalmente.

    A classe `Aula` existe — mas só no OUTRO perfil. Importar no perfil local
    tem de falhar, e não pegar emprestada a classe do vizinho.
    """
    Classe.objects.do_dono(perfil).filter(nome="Aula").delete()
    _classe(outro_perfil, "Aula", "#e8f0fe")

    with pytest.raises(CommandError, match="não existe"):
        importar(escrever(SPEC_BASE), aplicar=True)


@pytest.mark.django_db
def test_perfil_inexistente_e_erro(perfil, classes, escrever):
    with pytest.raises(CommandError, match="Perfil não encontrado"):
        importar(escrever(SPEC_BASE), aplicar=True, dono="ninguem@exemplo.com")


# --------------------------------------------------------------------------- #
# Conferência de tarefas (relata, não escreve)                                #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_nao_cria_tarefa(perfil, classes, escrever):
    from planner.models import Tarefa

    importar(escrever(SPEC_BASE), aplicar=True)

    assert not Tarefa.objects.do_dono(perfil).exists()


@pytest.mark.django_db
def test_relata_prova_sem_tarefa(perfil, classes, escrever, capsys):
    importar(escrever(SPEC_BASE), aplicar=True)

    saida = capsys.readouterr().out
    assert "sem tarefa" in saida
    assert "Prova 1" in saida


@pytest.mark.django_db
def test_prova_com_tarefa_no_prazo_nao_e_relatada(perfil, classes, escrever, capsys):
    from planner.tests.factories import TarefaFactory

    TarefaFactory(dono=perfil, deadline=aware(2026, 8, 31, 12))
    spec = {
        **SPEC_BASE,
        "datas": [{"data": "2026-08-31", "conteudo": "Revisão", "prova": "Prova 1"}],
    }
    importar(escrever(spec), aplicar=True)

    assert "Prova 1" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# O perfil local é o default                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_dono_default_e_o_perfil_local(perfil, classes, escrever):
    importar(escrever(SPEC_BASE), aplicar=True)

    ev = Evento.objects.do_dono(perfil).get()
    assert str(ev.dono_id) == perfis.UUID_PERFIL_LOCAL


@pytest.mark.django_db
def test_classe_prova_so_e_exigida_se_houver_prova(perfil, escrever):
    """Uma disciplina sem prova não deve exigir a classe Prova existindo."""
    _classe(perfil, "Aula", "#e8f0fe")
    Classe.objects.do_dono(perfil).filter(nome="Prova").delete()

    spec = {**SPEC_BASE, "datas": [{"data": "2026-08-17", "conteudo": "Sem prova"}]}
    importar(escrever(spec), aplicar=True)

    assert Ocorrencia.objects.get(data=date(2026, 8, 17)).classe_override is None
