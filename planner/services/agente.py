"""Agente conversacional (Marco C4, o "cérebro") — runtime de tool-use.

O MCP server (:8765) expõe as FERRAMENTAS; faltava o cérebro: um loop de LLM que
lê linguagem natural, decide quais ferramentas chamar, executa e responde. É a
metade não-construída do C4 (visão §5). O framework do modelo é a parte
TROCÁVEL: `AGENTE_PROVIDER` escolhe entre o Ollama local (mesma infra da Fase A,
fraco para agência multi-turno) e uma API remota (Anthropic) — solver, dados e
ferramentas seguem 100% locais.

Camada de ferramentas: chamadas **em processo** aos services (PR0 da Fase 0B —
antes era `requests` contra a própria API). Sem lógica de domínio aqui: o solver
continua a fonte de verdade e o agente só orquestra. Os contratos das ferramentas
são os mesmos que o MCP server embrulha (`mcp_server/server.py`) — o que mudou é
o transporte, não a forma.

Degrada como os irmãos (planejar-ia, cenários): provider fora/sem credencial/
timeout/resposta não-parseável ⇒ `AgenteIndisponivel`, e a task devolve uma
resposta honesta com `ia_indisponivel: true`.
"""

import json
from collections import namedtuple
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from planner.models import Classe
from planner.services import (
    agenda,
    aplicacao,
    completion,
    planejamento,
    replanejamento,
    tarefas,
    telemetria,
)
from planner.services.planejamento import HORIZONTES


class AgenteIndisponivel(Exception):
    """Provider desligado/sem credencial/timeout/resposta não-parseável."""


DIAS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


# --------------------------------------------------------------------------- #
# 1. Camada de ferramentas — chamadas EM PROCESSO aos services                 #
# --------------------------------------------------------------------------- #
# Até o PR0 da Fase 0B isto era `requests` contra a própria API (`API_BASE_URL`):
# código Django saindo pela rede para chegar onde já estava, atravessando auth,
# serialização e o ciclo de request. Com contas (Fase 0B) aquilo tomaria 401, e
# "resolver" significaria pôr credencial de usuário na fila do Celery. Chamando
# os services direto, o problema deixa de existir — e é mais rápido.
#
# O MCP server (`mcp_server/`) continua HTTP: é container separado servindo
# clientes externos, onde a fronteira é legítima.
#
# Contrato preservado: em erro, a ferramenta devolve `{"erro": ..., "detalhe":
# ...}` em vez de levantar. O loop lê o motivo e se recupera no turno seguinte.
def _erro(codigo, detalhe):
    return {"erro": codigo, "detalhe": detalhe}


# Toda ferramenta recebe `dono` como PRIMEIRO PARÂMETRO POSICIONAL, e o dispatch
# o passa por fora do `**tc.args` (Fase 0B, PR1). É estrutural, não convenção: os
# argumentos que o modelo escolhe e a identidade de quem está conversando chegam
# por caminhos diferentes, então nenhuma saída do LLM — alucinada ou induzida por
# texto na conversa — consegue trocar o dono dos dados.
def _listar_classes(dono):
    """Classes de atividade (id, nome). Use o id em criar_tarefa."""
    return [
        {"id": str(c.id), "nome": c.nome}
        for c in Classe.objects.do_dono(dono).order_by("nome")
    ]


def _criar_tarefa(
    dono,
    titulo,
    classe_id=None,
    deadline=None,
    esforco_min=None,
    descricao="",
    estrategia=None,
):
    prazo = None
    if deadline is not None:
        prazo = _normalizar_deadline(deadline)
        if prazo is None:
            return _erro(
                400,
                {
                    "deadline": [
                        f"{deadline!r} não é uma data ISO (use YYYY-MM-DDTHH:MM)."
                    ]
                },
            )
    try:
        tarefa = tarefas.criar(
            dono,
            titulo=titulo,
            classe_id=classe_id,
            deadline=prazo,
            esforco_min=esforco_min,
            descricao=descricao,
            estrategia=estrategia,
        )
    except tarefas.ClasseDesconhecida as e:
        # Erro acionável (E2E com o 7B): quando o modelo chuta um classe_id que
        # não existe, devolver as classes reais junto do erro permite que ele
        # corrija a chamada no turno seguinte, em vez de desistir com "problema
        # técnico".
        return {
            **_erro(400, {"classe_id": [str(e)]}),
            "classes_disponiveis": _listar_classes(dono),
            "dica": (
                "classe_id deve ser um id (UUID) de classes_disponiveis; "
                "repita criar_tarefa com o id correto."
            ),
        }
    except (ValueError, TypeError) as e:
        return _erro(400, str(e))
    return {
        "id": str(tarefa.id),
        "titulo": tarefa.titulo,
        "classe": tarefa.classe.nome if tarefa.classe else None,
        "deadline": tarefa.deadline.isoformat() if tarefa.deadline else None,
        "esforco_estimado": tarefa.esforco_estimado,
        "estrategia": tarefa.estrategia,
    }


def _atualizar_tarefa(
    dono,
    tarefa_id,
    deadline=None,
    esforco_min=None,
    estrategia=None,
    nao_antes_de=None,
    nao_depois_de=None,
    janela_inicio=None,
    janela_fim=None,
    dias_permitidos=None,
    limpar=None,
):
    """Altera uma tarefa que já existe.

    Existe porque faltava: no dogfooding de 15/08/2026, pedir "faça essas duas
    terminarem na véspera da prova" fez o modelo chamar `criar_tarefa` e
    **duplicar** as tarefas — a única ferramenta de escrita que ele tinha. Não
    era limitação do modelo; era ferramenta ausente.

    Só campos de agendamento (ver `tarefas.CAMPOS_ATUALIZAVEIS`): `titulo` e
    `descricao` ficam de fora porque são texto do usuário, e a descrição virou
    insumo de planejamento — a IA reescrevê-la seria editar a própria entrada.

    Argumento nulo é **ignorado**, não apagado: o modelo omite o que não quis
    mexer, e um `None` acidental não pode zerar restrição alheia. Para limpar de
    verdade existe `limpar`, uma lista explícita de nomes de campo.
    """
    campos = {
        "deadline": _normalizar_deadline(deadline) if deadline else None,
        "esforco_estimado": esforco_min,
        "estrategia": estrategia,
        "nao_antes_de": _data_simples(nao_antes_de),
        "nao_depois_de": _data_simples(nao_depois_de),
        "janela_inicio": _hora_simples(janela_inicio),
        "janela_fim": _hora_simples(janela_fim),
        "dias_permitidos": dias_permitidos,
    }
    if deadline and campos["deadline"] is None:
        return _erro(400, {"deadline": [f"{deadline!r} não é uma data ISO."]})
    for campo, cru in (
        ("nao_antes_de", nao_antes_de),
        ("nao_depois_de", nao_depois_de),
        ("janela_inicio", janela_inicio),
        ("janela_fim", janela_fim),
    ):
        if cru and campos[campo] is None:
            return _erro(400, {campo: [f"{cru!r} não tem o formato esperado."]})

    alterar = {c: v for c, v in campos.items() if v is not None}
    for campo in limpar or []:
        if campo not in tarefas.CAMPOS_ATUALIZAVEIS:
            return _erro(400, {"limpar": [f"{campo!r} não é um campo atualizável."]})
        alterar[campo] = None
    if not alterar:
        return _erro(400, "informe ao menos um campo para alterar.")

    try:
        tarefa = tarefas.atualizar(dono, tarefa_id, **alterar)
    except tarefas.TarefaDesconhecida as e:
        return _erro(404, str(e))
    except tarefas.ParametrosInvalidos as e:
        return _erro(400, e.erros)
    except (ValueError, TypeError) as e:
        return _erro(400, str(e))

    from . import vocabulario

    # Confirmação em português, escrita pelo CÓDIGO — o modelo só copia. Mesma
    # razão da tabela de vocabulário do PR C.
    ajuste = {c: v for c, v in alterar.items() if v is not None}
    return {
        "id": str(tarefa.id),
        "titulo": tarefa.titulo,
        "alterado": sorted(alterar),
        "resumo": vocabulario.descrever(
            {
                **ajuste,
                "janela_inicio": (
                    tarefa.janela_inicio.strftime("%H:%M")
                    if tarefa.janela_inicio and "janela_inicio" in alterar
                    else None
                ),
                "janela_fim": (
                    tarefa.janela_fim.strftime("%H:%M")
                    if tarefa.janela_fim and "janela_fim" in alterar
                    else None
                ),
            }
        ),
    }


def _listar_pendentes(dono):
    """Pendentes pré-digeridos (mesma razão da agenda: o modelo copia)."""
    return [
        {
            "evento_id": str(ev.id),
            "titulo": ev.titulo,
            "venceu_em": timezone.localtime(ev.fim).strftime("%Y-%m-%d %H:%M"),
            "classe": ev.classe.nome if ev.classe else None,
        }
        for ev in agenda.pendentes(dono, timezone.now())
    ]


def _normalizar_deadline(valor):
    """O usuário fala hora LOCAL; o 7B às vezes escreve a hora literal com Z
    ("17h" → 17:00Z = 14h local — visto no E2E). Regra do app single-user:
    naive ou UTC-zero = hora de parede local (o 7B nunca converte fuso de
    verdade); offset explícito não-zero é respeitado.

    Devolve datetime tz-aware, ou None se não for ISO — antes do PR0 o valor
    cru passava reto e quem recusava era a API; agora quem recusa é a
    ferramenta, com a mesma mensagem acionável."""
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return timezone.make_aware(dt)
    if dt.utcoffset() and dt.utcoffset().total_seconds() != 0:
        return dt
    return timezone.make_aware(dt.replace(tzinfo=None))


def _data_simples(valor):
    """ "YYYY-MM-DD" → `date`; None/inválido → None (quem chama vira erro)."""
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except (ValueError, TypeError):
        return None


def _hora_simples(valor):
    """ "HH:MM" → `time`; None/inválido → None (quem chama vira erro)."""
    if not valor:
        return None
    try:
        h, m = str(valor).split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _normalizar_janela(valor, eh_fim=False):
    """Aceita o que o modelo mandar ("2026-07-06", "...T00:00", com/sem offset)
    e devolve o datetime tz-aware. Data pura como fim = fim do dia.
    Valor não-ISO devolve None — quem chama transforma em erro legível."""
    try:
        dt = datetime.fromisoformat(str(valor))
    except (ValueError, TypeError):
        return None
    if eh_fim and len(str(valor)) == 10:  # só a data: janela até 23:59
        dt = dt.replace(hour=23, minute=59)
    if dt.tzinfo is None:
        dt = timezone.make_aware(dt)
    return dt


def _consultar_agenda(dono, inicio, fim):
    """Agenda PRÉ-DIGERIDA: dias com eventos, horários locais hh:mm, campos
    mínimos. O payload cru da API (UTC, dezenas de campos) fazia o 7B alucinar
    o resumo — chamava a ferramenta certa e narrava outra semana. Entregar o
    resumo pronto reduz a tarefa do modelo a copiar (e corta tokens: mais
    rápido e mais barato de contexto)."""
    ini_dt = _normalizar_janela(inicio)
    fim_dt = _normalizar_janela(fim, eh_fim=True)
    if ini_dt is None or fim_dt is None:
        return _erro(400, "inicio/fim devem ser datas ISO (YYYY-MM-DD ou completo).")

    try:
        itens = agenda.eventos_na_janela(dono, ini_dt, fim_dt)
    except agenda.JanelaInvalida as e:
        return _erro(400, str(e))

    dias = {}
    for item in itens:
        ev = item.evento
        ini = timezone.localtime(agenda.inicio_efetivo(item))
        fim_ev = timezone.localtime(item.ocorrencia.fim if item.ocorrencia else ev.fim)
        alvo = item.ocorrencia or ev
        dias.setdefault(ini.date(), []).append(
            {
                "evento_id": str(ev.id),
                "titulo": ev.titulo,
                "inicio": ini.strftime("%H:%M"),
                "fim": fim_ev.strftime("%H:%M"),
                "classe": ev.classe.nome if ev.classe else None,
                "status": completion.status_efetivo(alvo) or alvo.status,
            }
        )
    return [
        {
            "data": d.isoformat(),
            "dia_da_semana": DIAS_PT[d.weekday()],
            "eventos": sorted(evs, key=lambda e: e["inicio"]),
        }
        for d, evs in sorted(dias.items())
    ]


def _simular_plano(
    dono, tarefa_ids, preferencias=None, horizonte=None, a_partir_de=None
):
    """What-if: roda o solver e NÃO persiste (mesmo contrato de /calcular)."""
    validas, invalidas = planejamento.validar_tarefas(dono, tarefa_ids)
    if invalidas:
        return _erro(422, {"tarefas_invalidas": invalidas})

    agora = _normalizar_janela(a_partir_de) if a_partir_de else timezone.now()
    if agora is None:
        return _erro(400, "a_partir_de deve ser uma data ISO.")

    res = planejamento.montar_plano(
        dono,
        validas,
        agora,
        preferencias or {},
        horizonte_dias=HORIZONTES.get(horizonte) if horizonte else None,
    )
    return planejamento.serializar_plano(res)


def _aplicar_plano(
    dono, tarefa_ids, preferencias=None, horizonte=None, a_partir_de=None
):
    """Recalcula o plano com os MESMOS argumentos da simulação e PERSISTE.

    Fecha o buraco que o dogfooting achou: `simular_plano` aceitava
    `a_partir_de` e não gravava; `replanejar` gravava e não aceitava. Sem esta
    ferramenta, um plano montado com `a_partir_de` não tinha como virar
    calendário — nenhum prompt resolveria isso.

    **O plano não volta pelo modelo.** Recebe os mesmos argumentos curtos de
    `simular_plano` e re-roda o solver aqui dentro, em vez de aceitar uma lista
    de sessões que o LLM teria de copiar de volta. É a mesma razão de
    `_consultar_agenda` devolver resumo pronto: payload grande atravessando o 7B
    volta corrompido. De quebra é o que a view `/replanejar/aplicar` já faz, e
    pelo mesmo motivo declarado lá — "evita aplicar plano obsoleto".

    A releitura pode divergir do que foi simulado se a agenda mudou no meio; o
    retorno descreve o que foi REALMENTE criado, nunca o que se pretendia criar.

    Chamar duas vezes não duplica: as tarefas viram PROMOVIDA na primeira, e
    `validar_tarefas` as recusa na segunda ("tarefa já promovida").
    """
    validas, invalidas = planejamento.validar_tarefas(dono, tarefa_ids)
    if invalidas:
        return _erro(422, {"tarefas_invalidas": invalidas})

    agora = _normalizar_janela(a_partir_de) if a_partir_de else timezone.now()
    if agora is None:
        return _erro(400, "a_partir_de deve ser uma data ISO.")

    res = planejamento.montar_plano(
        dono,
        validas,
        agora,
        preferencias or {},
        horizonte_dias=HORIZONTES.get(horizonte) if horizonte else None,
    )
    if not res.sessoes:
        return _erro(
            422,
            {
                "motivo": "o solver não achou espaço para nenhuma sessão",
                "nao_alocado": [vars(n) for n in res.nao_alocado],
            },
        )

    try:
        criados = aplicacao.aplicar_sessoes(
            dono, planejamento.serializar_plano(res)["sessoes"]
        )
    except aplicacao.AplicacaoInvalida as e:
        return _erro(400, e.erros)

    # Resumo por tarefa, em horário local e já digerido — o modelo copia em vez
    # de recalcular (mesma disciplina de `_consultar_agenda`).
    por_tarefa = {}
    for s in res.sessoes:
        info = por_tarefa.setdefault(
            s.tarefa_id,
            {
                "tarefa": s.tarefa_titulo,
                "sessoes": 0,
                "minutos": 0,
                "de": None,
                "ate": None,
            },
        )
        dia = timezone.localtime(s.inicio).date()
        info["sessoes"] += 1
        info["minutos"] += s.dur_min
        info["de"] = (
            dia.isoformat() if info["de"] is None else min(info["de"], dia.isoformat())
        )
        info["ate"] = (
            dia.isoformat()
            if info["ate"] is None
            else max(info["ate"], dia.isoformat())
        )

    return {
        "eventos_criados": len(criados),
        "aplicado": list(por_tarefa.values()),
        "nao_alocado": [vars(n) for n in res.nao_alocado],
    }


def _preferencias_da_chamada(janela_inicio, janela_fim, evitar_fds, max_min_por_dia):
    """Monta o dict de preferências a partir dos argumentos nomeados da ferramenta.

    Nomeadas, e não um dict livre: o modelo escolhe melhor entre parâmetros com
    nome do que dentro de um objeto aninhado, e cada uma é validável aqui. Sem
    isto, `montar_preferencias` levantaria `ValueError` no meio do solver com um
    "HH:MM" torto — e ferramenta que levanta quebra o contrato do loop (erro é
    dict, nunca exceção).

    Devolve `(preferencias, erro)`; só um dos dois é não-nulo.
    """
    prefs = {}
    for nome, valor in (("janela_inicio", janela_inicio), ("janela_fim", janela_fim)):
        if valor is None:
            continue
        if _hora_simples(valor) is None:
            return None, _erro(400, {nome: [f"{valor!r} não é um horário HH:MM."]})
        prefs[nome] = valor
    if ("janela_inicio" in prefs) != ("janela_fim" in prefs):
        return None, _erro(
            400, {"janela_inicio": ["janela_inicio e janela_fim andam juntas."]}
        )
    if prefs and _hora_simples(prefs["janela_inicio"]) >= _hora_simples(
        prefs["janela_fim"]
    ):
        return None, _erro(
            400, {"janela_fim": ["janela_fim deve ser maior que janela_inicio."]}
        )
    if evitar_fds is not None:
        if not isinstance(evitar_fds, bool):
            return None, _erro(400, {"evitar_fds": ["Deve ser true ou false."]})
        prefs["evitar_fds"] = evitar_fds
    if max_min_por_dia is not None:
        try:
            teto = int(max_min_por_dia)
        except (TypeError, ValueError):
            return None, _erro(400, {"max_min_por_dia": ["Deve ser um inteiro ≥ 1."]})
        if teto < 1:
            return None, _erro(400, {"max_min_por_dia": ["Deve ser um inteiro ≥ 1."]})
        prefs["max_min_por_dia_por_tarefa"] = teto
    return prefs, None


def _replanejar(
    dono,
    dias_bloqueados=None,
    preferencias=None,
    aplicar=False,
    janela_inicio=None,
    janela_fim=None,
    evitar_fds=None,
    max_min_por_dia=None,
):
    """Replaneja do agora em diante. `aplicar=False` só simula (plano + diff).

    As preferências são **da chamada**, não da pessoa: valem para este plano e
    não ficam gravadas em lugar nenhum (o `Perfil` ainda não guarda preferência).
    Quem pedir "estude só de manhã" precisará repetir no próximo replanejamento —
    limitação conhecida, registrada no "Estado atual" do CLAUDE.md.
    """
    da_chamada, erro = _preferencias_da_chamada(
        janela_inicio, janela_fim, evitar_fds, max_min_por_dia
    )
    if erro:
        return erro
    preferencias = {**(preferencias or {}), **da_chamada}

    agora = timezone.now()
    try:
        if aplicar:
            rp, criados, removidos = replanejamento.aplicar_replanejamento(
                dono,
                agora=agora,
                dias_bloqueados=dias_bloqueados,
                preferencias=preferencias or {},
            )
            return {
                "diff": rp.diff,
                "eventos_criados": criados,
                "eventos_removidos": removidos,
                "metricas": rp.metricas,
                "metricas_vs_anterior": rp.metricas_vs_anterior,
            }
        rp = replanejamento.replanejar(
            dono,
            agora=agora,
            dias_bloqueados=dias_bloqueados,
            preferencias=preferencias or {},
        )
    except aplicacao.AplicacaoInvalida as e:
        return _erro(400, e.erros)
    return {
        "plano": planejamento.serializar_plano(rp.res),
        "diff": rp.diff,
        "metricas": rp.metricas,
        "metricas_vs_anterior": rp.metricas_vs_anterior,
    }


# Registro: cada ferramenta declara nome, descrição, JSON Schema dos parâmetros,
# o executor e se MUDA ESTADO (o front usa isso para recarregar o calendário).
FERRAMENTAS = [
    {
        "nome": "listar_classes",
        "descricao": "Lista as classes de atividade (id, nome). Use o id em criar_tarefa.",
        "parametros": {"type": "object", "properties": {}},
        "executar": _listar_classes,
        "muda_estado": False,
    },
    {
        "nome": "criar_tarefa",
        "descricao": (
            "Cria uma tarefa no Inbox. Para ela entrar num plano precisa de "
            "deadline (ISO-8601 com offset), esforco_min (minutos) e classe_id "
            "(veja listar_classes). Use estrategia='TARDE' quando o valor da "
            "tarefa estiver em fazê-la PERTO do prazo (estudar para prova é o "
            "caso típico); 'CEDO' quando quanto antes melhor. Sem isso o plano "
            "a agenda o quanto antes — estudo de prova cairia semanas antes."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string"},
                "classe_id": {"type": "string"},
                "deadline": {"type": "string", "description": "ISO-8601 com offset"},
                "esforco_min": {"type": "integer", "description": "minutos"},
                "descricao": {"type": "string"},
                "estrategia": {
                    "type": "string",
                    "description": "CEDO | TARDE",
                },
            },
            "required": ["titulo"],
        },
        "executar": _criar_tarefa,
        "muda_estado": True,
    },
    {
        "nome": "atualizar_tarefa",
        "descricao": (
            "Altera uma tarefa que JÁ EXISTE (nunca use criar_tarefa para isso). "
            "Campos: deadline, esforco_min, estrategia (CEDO|TARDE), "
            "nao_antes_de e nao_depois_de (YYYY-MM-DD), janela_inicio e "
            "janela_fim (HH:MM, andam juntas), dias_permitidos (0=segunda … "
            "6=domingo). Omita o que não vai mudar. Para uma tarefa terminar "
            "antes do prazo, use nao_depois_de. Para apagar uma restrição, "
            "passe o nome dela em `limpar`."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "tarefa_id": {"type": "string"},
                "deadline": {"type": "string"},
                "esforco_min": {"type": "integer"},
                "estrategia": {"type": "string"},
                "nao_antes_de": {"type": "string"},
                "nao_depois_de": {"type": "string"},
                "janela_inicio": {"type": "string"},
                "janela_fim": {"type": "string"},
                "dias_permitidos": {"type": "array", "items": {"type": "integer"}},
                "limpar": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tarefa_id"],
        },
        "executar": _atualizar_tarefa,
        "muda_estado": True,
    },
    {
        "nome": "listar_pendentes",
        "descricao": "Eventos rastreáveis já vencidos e não concluídos (status PENDENTE).",
        "parametros": {"type": "object", "properties": {}},
        "executar": _listar_pendentes,
        "muda_estado": False,
    },
    {
        "nome": "consultar_agenda",
        "descricao": (
            "Agenda entre `inicio` e `fim` (basta YYYY-MM-DD; fuso e fim-do-dia "
            "são automáticos), JÁ RESUMIDA: lista de dias {data, dia_da_semana, "
            "eventos[{titulo, inicio, fim, classe}]} com horários LOCAIS hh:mm "
            "— apenas copie, não recalcule. Dia ausente = sem eventos. Use "
            "para 'como está minha semana'."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "inicio": {"type": "string", "description": "data/hora ISO"},
                "fim": {"type": "string", "description": "data/hora ISO"},
            },
            "required": ["inicio", "fim"],
        },
        "executar": _consultar_agenda,
        "muda_estado": False,
    },
    {
        "nome": "simular_plano",
        "descricao": (
            "What-if: monta um plano para as tarefas SEM persistir nada. "
            "horizonte: AUTOMATICO | SEMANA | DUAS_SEMANAS | MES."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "tarefa_ids": {"type": "array", "items": {"type": "string"}},
                "horizonte": {"type": "string"},
                "a_partir_de": {"type": "string"},
            },
            "required": ["tarefa_ids"],
        },
        "executar": _simular_plano,
        "muda_estado": False,
    },
    {
        "nome": "aplicar_plano",
        "descricao": (
            "GRAVA no calendário o plano das tarefas indicadas: recalcula com os "
            "mesmos argumentos de simular_plano e cria os eventos. Use DEPOIS de "
            "simular e o usuário concordar. Mesmos parâmetros de simular_plano — "
            "inclusive a_partir_de, que é como um estudo fica colado na prova."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "tarefa_ids": {"type": "array", "items": {"type": "string"}},
                "horizonte": {"type": "string"},
                "a_partir_de": {"type": "string"},
            },
            "required": ["tarefa_ids"],
        },
        "executar": _aplicar_plano,
        "muda_estado": True,
    },
    {
        "nome": "replanejar",
        "descricao": (
            "Replaneja a agenda do agora em diante. aplicar=false simula "
            "(nada persiste); aplicar=true substitui as sessões futuras. "
            "'Livra meu sábado' = dias_bloqueados=['<data do sábado>']. "
            "Para mudar horários do plano use janela_inicio/janela_fim (HH:MM), "
            "evitar_fds e max_min_por_dia — valem só para este replanejamento."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "dias_bloqueados": {"type": "array", "items": {"type": "string"}},
                "aplicar": {"type": "boolean"},
                "janela_inicio": {
                    "type": "string",
                    "description": "HH:MM — hora mais cedo do dia; anda com janela_fim",
                },
                "janela_fim": {"type": "string", "description": "HH:MM"},
                "evitar_fds": {
                    "type": "boolean",
                    "description": "false libera sábado e domingo",
                },
                "max_min_por_dia": {
                    "type": "integer",
                    "description": "teto de minutos por dia para cada tarefa",
                },
            },
        },
        "executar": _replanejar,
        "muda_estado": True,  # marcado quando aplicar=true (ver dispatch abaixo)
    },
]

FERRAMENTAS_POR_NOME = {f["nome"]: f for f in FERRAMENTAS}


# --------------------------------------------------------------------------- #
# 2. Providers — o cérebro trocável (mesma interface, conversa nativa dentro)  #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "Você é o assistente de rotina de um planejador de estudos (single-user, "
    "pt-BR). O usuário pede mudanças em linguagem natural; você as executa "
    "chamando as ferramentas (criar tarefa, consultar agenda, replanejar, "
    "simular). O SOLVER é a fonte de verdade — NUNCA invente horários, datas ou "
    "números; use as ferramentas e reporte o que elas devolverem. IDS NUNCA são "
    "inventados: id de classe vem de listar_classes (campo id, um UUID) — "
    "chame-a ANTES de criar_tarefa quando o usuário citar uma classe pelo "
    "nome. DATAS: resolva 'sexta'/'segunda que vem' copiando a data da chave "
    "correspondente em `datas` nos FATOS (ex.: 'próxima segunda-feira') — "
    "NUNCA conte dias de cabeça. Se uma ferramenta devolver "
    "erro, leia o motivo (e a dica, se houver), corrija os argumentos e tente "
    "de novo antes de desistir. Ao "
    "terminar, responda em uma ou duas frases objetivas, em português, dizendo "
    "o que fez ou encontrou. Se faltar um dado essencial (ex.: a classe da "
    "tarefa), pergunte em vez de adivinhar. "
    # A regra abaixo já existia no prompt do planejador (planejamento_ia.py) e
    # faltava aqui — foi por isso que o agente respondeu ao usuário com
    # "classe_id: c9a351f9-...". Prompt não basta (há teste que barra), mas a
    # ausência dele era um convite.
    "LINGUAGEM: quem lê a resposta não conhece o sistema por dentro. NUNCA "
    "escreva id/UUID, nome de campo ou parâmetro (classe_id, tarefa_id, "
    "estrategia, buffer_dias, max_min_por_dia, nao_antes_de…), nem nomes de "
    "ferramenta. Refira-se às coisas pelo TÍTULO e descreva em português comum "
    "(ex.: 'estuda perto do prazo', 'só de manhã', 'no máximo 2h por dia'). "
    "Sem linguagem floreada."
)

# Teto do loop de tool-use: cobre o encadeamento típico (listar_classes →
# criar_tarefa) com folga; acima disso é sinal de o modelo estar patinando.
MAX_ITERACOES = 6

_Turno = namedtuple("_Turno", "texto tool_calls")
_ToolCall = namedtuple("_ToolCall", "id nome args")


class _OllamaProvider:
    """Cérebro local (Ollama). Reusa OLLAMA_* da Fase A. O 7B/CPU dá conta de
    pedidos de 1–2 ferramentas; a própria visão avisa que agência multi-turno
    profunda pede modelo maior (use AGENTE_PROVIDER=anthropic para isso)."""

    def __init__(self, historico, mensagem, dono_id=None):
        import ollama

        self._dono_id = dono_id

        self._cli = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT
        )
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": f["nome"],
                    "description": f["descricao"],
                    "parameters": f["parametros"],
                },
            }
            for f in FERRAMENTAS
        ]
        self._mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *historico,
            {"role": "user", "content": mensagem},
        ]

    def _chamar(self):
        # Uma medição POR CHAMADA, não por turno: o loop de tool use faz até
        # MAX_ITERACOES idas ao modelo, e é justamente esse custo multiplicado
        # que a Fase 2 precisa enxergar (0A.3).
        with telemetria.medir(
            "agente", "ollama", settings.OLLAMA_MODEL, dono_id=self._dono_id
        ) as m:
            try:
                resp = self._cli.chat(
                    model=settings.OLLAMA_MODEL,
                    messages=self._mensagens,
                    tools=self._tools,
                    options={"temperature": 0},
                )
            except Exception as e:  # rede, timeout, etc.
                raise AgenteIndisponivel(str(e))
            m.metadados(**telemetria.de_ollama(resp))
        msg = resp["message"]
        assistente = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            assistente["tool_calls"] = msg["tool_calls"]
        self._mensagens.append(assistente)  # eco do turno (mantém o fio)

        chamadas = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            chamadas.append(_ToolCall(id=str(i), nome=fn["name"], args=dict(args)))
        return _Turno(texto=(msg.get("content") or "").strip(), tool_calls=chamadas)

    def gerar(self):
        return self._chamar()

    def responder_ferramentas(self, resultados):
        for _id, conteudo in resultados:
            self._mensagens.append(
                {"role": "tool", "content": json.dumps(conteudo, ensure_ascii=False)}
            )
        return self._chamar()


class _AnthropicProvider:
    """Cérebro remoto (API da Claude). O que a visão C4 recomenda para agência
    multi-turno com tool use; solver e dados permanecem locais."""

    def __init__(self, historico, mensagem, dono_id=None):
        self._dono_id = dono_id
        try:
            import anthropic
        except ImportError as e:  # dep opcional (só quando AGENTE_PROVIDER=anthropic)
            raise AgenteIndisponivel("pacote 'anthropic' não instalado") from e
        if not settings.ANTHROPIC_API_KEY:
            raise AgenteIndisponivel("ANTHROPIC_API_KEY não configurada")
        self._cli = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._tools = [
            {
                "name": f["nome"],
                "description": f["descricao"],
                "input_schema": f["parametros"],
            }
            for f in FERRAMENTAS
        ]
        # O histórico é só texto (user/assistant) — mesma forma que a Claude aceita.
        self._mensagens = [*historico, {"role": "user", "content": mensagem}]

    def _chamar(self):
        with telemetria.medir(
            "agente", "anthropic", settings.AGENTE_MODEL, dono_id=self._dono_id
        ) as m:
            try:
                resp = self._cli.messages.create(
                    model=settings.AGENTE_MODEL,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=self._mensagens,
                    tools=self._tools,
                )
            except Exception as e:
                raise AgenteIndisponivel(str(e))
            m.metadados(**telemetria.de_anthropic(resp))
        # Eco do turno do assistente (blocos nativos: text + tool_use).
        self._mensagens.append({"role": "assistant", "content": resp.content})
        texto, chamadas = "", []
        for bloco in resp.content:
            if bloco.type == "text":
                texto += bloco.text
            elif bloco.type == "tool_use":
                chamadas.append(
                    _ToolCall(
                        id=bloco.id, nome=bloco.name, args=dict(bloco.input or {})
                    )
                )
        return _Turno(texto=texto.strip(), tool_calls=chamadas)

    def gerar(self):
        return self._chamar()

    def responder_ferramentas(self, resultados):
        self._mensagens.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": _id,
                        "content": json.dumps(conteudo, ensure_ascii=False),
                    }
                    for _id, conteudo in resultados
                ],
            }
        )
        return self._chamar()


def _criar_provider(historico, mensagem, dono_id=None):
    nome = (settings.AGENTE_PROVIDER or "ollama").lower()
    if nome == "anthropic":
        return _AnthropicProvider(historico, mensagem, dono_id)
    if nome == "ollama":
        return _OllamaProvider(historico, mensagem, dono_id)
    raise AgenteIndisponivel(f"AGENTE_PROVIDER desconhecido: {nome}")


# --------------------------------------------------------------------------- #
# 3. Loop de tool-use — provider-agnóstico                                     #
# --------------------------------------------------------------------------- #
def conversar(dono, mensagem, contexto, historico=None):
    """Um turno de conversa, no escopo de um perfil. Roda o loop de tool-use e
    devolve `{resposta, acoes, mudou_estado, ia_indisponivel}`.

    `contexto` (data de hoje, seleção atual, etc.) entra como FATOS no início do
    pedido — o agente resolve "sexta"/"meu sábado" a partir daí, não inventa.
    `historico` é a conversa anterior (lista {role, content}, só texto).
    Levanta `AgenteIndisponivel` se o cérebro estiver fora/desligado.

    O `dono` **não** entra nos FATOS nem no prompt: ele fica fora do alcance do
    modelo e é aplicado no dispatch das ferramentas.
    """
    if not settings.AGENTE_ENABLED:
        raise AgenteIndisponivel("agente desligado")

    # Grounding determinístico (E2E com o 7B): modelos pequenos não fazem o
    # salto de descoberta (listar_classes → criar_tarefa) com confiança — chutam
    # ids. As classes são poucas e estáveis: entram como FATOS, e o id certo é
    # questão de copiar, não de agência.
    fatos = dict(contexto or {})
    if "classes" not in fatos:
        # Antes do PR0 isto era uma chamada HTTP que podia falhar, e havia um
        # guarda para "segue sem as classes". Em processo, falha aqui é o banco
        # fora — não há degradação útil: a task inteira cai e o endpoint já
        # responde com `ia_indisponivel`.
        fatos["classes"] = _listar_classes(dono)
    # Data é aritmética, não agência: o 7B erra "segunda que vem" contando nos
    # dedos (e ignorava a tabela genérica de dias). O dicionário usa as MESMAS
    # palavras que o usuário diria como chave — a resolução vira busca literal.
    if "datas" not in fatos:
        hoje_local = timezone.localdate()
        datas = {
            "hoje": f"{hoje_local.isoformat()} ({DIAS_PT[hoje_local.weekday()]})",
            "amanhã": (hoje_local + timedelta(days=1)).isoformat(),
        }
        for i in range(1, 8):
            d = hoje_local + timedelta(days=i)
            datas[f"próxima {DIAS_PT[d.weekday()]}"] = d.isoformat()
        fatos["datas"] = datas

    pedido = (
        "FATOS (use só isto para resolver datas e ids; não invente):\n"
        + json.dumps(fatos, ensure_ascii=False)
        + "\n\nPedido do usuário: "
        + mensagem
    )
    prov = _criar_provider(historico or [], pedido, dono_id=dono.id)

    acoes = []
    turno = prov.gerar()
    iters = 0
    while turno.tool_calls and iters < MAX_ITERACOES:
        iters += 1
        resultados = []
        for tc in turno.tool_calls:
            ferr = FERRAMENTAS_POR_NOME.get(tc.nome)
            if ferr is None:
                resultados.append(
                    (tc.id, {"erro": "ferramenta desconhecida", "nome": tc.nome})
                )
                continue
            try:
                # `dono` posicional, `tc.args` desempacotado: o que o modelo
                # escolheu nunca chega perto de decidir de quem são os dados.
                saida = ferr["executar"](dono, **tc.args)
            except TypeError as e:  # argumentos que não batem com a assinatura
                saida = {"erro": "argumentos inválidos", "detalhe": str(e)}
            ok = not (isinstance(saida, dict) and "erro" in saida)
            # replanejar só muda estado quando aplicar=true; as demais são fixas.
            muda = ferr["muda_estado"] and (
                tc.nome != "replanejar" or bool(tc.args.get("aplicar"))
            )
            resultados.append((tc.id, saida))
            acoes.append(
                {"ferramenta": tc.nome, "args": tc.args, "muda_estado": muda, "ok": ok}
            )
        turno = prov.responder_ferramentas(resultados)

    return {
        "resposta": turno.texto,
        "acoes": acoes,
        "mudou_estado": any(a["muda_estado"] and a["ok"] for a in acoes),
        "ia_indisponivel": False,
    }
