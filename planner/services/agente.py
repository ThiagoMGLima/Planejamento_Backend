"""Agente conversacional (Marco C4, o "cérebro") — runtime de tool-use.

O MCP server (:8765) expõe as FERRAMENTAS; faltava o cérebro: um loop de LLM que
lê linguagem natural, decide quais ferramentas chamar, executa e responde. É a
metade não-construída do C4 (visão §5). O framework do modelo é a parte
TROCÁVEL: `AGENTE_PROVIDER` escolhe entre o Ollama local (mesma infra da Fase A,
fraco para agência multi-turno) e uma API remota (Anthropic) — solver, dados e
ferramentas seguem 100% locais.

Camada de ferramentas: reusa os MESMOS contratos HTTP validados que o MCP server
embrulha (`mcp_server/server.py`) — chamadas `requests` à API local
(`API_BASE_URL`), sem lógica de domínio aqui. O solver continua a fonte de
verdade; o agente só orquestra.

Degrada como os irmãos (planejar-ia, cenários): provider fora/sem credencial/
timeout/resposta não-parseável ⇒ `AgenteIndisponivel`, e a task devolve uma
resposta honesta com `ia_indisponivel: true`.
"""

import json
from collections import namedtuple

import requests
from django.conf import settings


class AgenteIndisponivel(Exception):
    """Provider desligado/sem credencial/timeout/resposta não-parseável."""


# --------------------------------------------------------------------------- #
# 1. Camada de ferramentas — mesmos contratos HTTP do MCP server              #
# --------------------------------------------------------------------------- #
def _api(metodo, caminho, corpo=None, params=None):
    """Chama a API local e devolve o corpo. Erro (HTTP/rede) vira dict — o agente
    lê o motivo e se recupera, em vez de estourar o loop."""
    url = f"{settings.API_BASE_URL.rstrip('/')}{caminho}"
    try:
        resp = requests.request(metodo, url, json=corpo, params=params, timeout=60)
    except requests.RequestException as e:
        return {"erro": "rede", "detalhe": str(e)}
    try:
        dados = resp.json()
    except ValueError:
        dados = {"detalhe": resp.text}
    if resp.status_code >= 400:
        return {"erro": resp.status_code, "detalhe": dados}
    return dados


def _listar_classes():
    """Classes de atividade (id, nome). Use o id em criar_tarefa."""
    r = _api("GET", "/classes/")
    return r["results"] if isinstance(r, dict) and "results" in r else r


def _criar_tarefa(
    titulo, classe_id=None, deadline=None, esforco_min=None, descricao=""
):
    corpo = {"titulo": titulo, "descricao": descricao}
    if classe_id is not None:
        corpo["classe_id"] = classe_id
    if deadline is not None:
        corpo["deadline"] = deadline
    if esforco_min is not None:
        corpo["esforco_estimado"] = esforco_min
    return _api("POST", "/tarefas/", corpo=corpo)


def _listar_pendentes():
    return _api("GET", "/pendentes")


def _consultar_agenda(inicio, fim):
    return _api("GET", "/eventos/", params={"inicio": inicio, "fim": fim})


def _simular_plano(tarefa_ids, preferencias=None, horizonte=None, a_partir_de=None):
    corpo = {"tarefa_ids": tarefa_ids}
    if preferencias:
        corpo["preferencias"] = preferencias
    if horizonte:
        corpo["horizonte"] = horizonte
    if a_partir_de:
        corpo["a_partir_de"] = a_partir_de
    return _api("POST", "/planejamento/calcular", corpo=corpo)


def _replanejar(dias_bloqueados=None, preferencias=None, aplicar=False):
    corpo = {}
    if dias_bloqueados:
        corpo["dias_bloqueados"] = dias_bloqueados
    if preferencias:
        corpo["preferencias"] = preferencias
    caminho = (
        "/planejamento/replanejar/aplicar" if aplicar else "/planejamento/replanejar"
    )
    return _api("POST", caminho, corpo=corpo)


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
            "(veja listar_classes)."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string"},
                "classe_id": {"type": "string"},
                "deadline": {"type": "string", "description": "ISO-8601 com offset"},
                "esforco_min": {"type": "integer", "description": "minutos"},
                "descricao": {"type": "string"},
            },
            "required": ["titulo"],
        },
        "executar": _criar_tarefa,
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
            "Lista os eventos agendados entre `inicio` e `fim` (datas ISO). "
            "Use para responder 'como está minha semana'."
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
        "nome": "replanejar",
        "descricao": (
            "Replaneja a agenda do agora em diante. aplicar=false simula "
            "(nada persiste); aplicar=true substitui as sessões futuras. "
            "'Livra meu sábado' = dias_bloqueados=['<data do sábado>']."
        ),
        "parametros": {
            "type": "object",
            "properties": {
                "dias_bloqueados": {"type": "array", "items": {"type": "string"}},
                "aplicar": {"type": "boolean"},
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
    "números; use as ferramentas e reporte o que elas devolverem. Ao terminar, "
    "responda em uma ou duas frases objetivas, em português, dizendo o que fez "
    "ou encontrou. Se faltar um dado essencial (ex.: a classe da tarefa), "
    "pergunte em vez de adivinhar."
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

    def __init__(self, historico, mensagem):
        import ollama

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
        try:
            resp = self._cli.chat(
                model=settings.OLLAMA_MODEL,
                messages=self._mensagens,
                tools=self._tools,
                options={"temperature": 0},
            )
        except Exception as e:  # rede, timeout, etc.
            raise AgenteIndisponivel(str(e))
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

    def __init__(self, historico, mensagem):
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


def _criar_provider(historico, mensagem):
    nome = (settings.AGENTE_PROVIDER or "ollama").lower()
    if nome == "anthropic":
        return _AnthropicProvider(historico, mensagem)
    if nome == "ollama":
        return _OllamaProvider(historico, mensagem)
    raise AgenteIndisponivel(f"AGENTE_PROVIDER desconhecido: {nome}")


# --------------------------------------------------------------------------- #
# 3. Loop de tool-use — provider-agnóstico                                     #
# --------------------------------------------------------------------------- #
def conversar(mensagem, contexto, historico=None):
    """Um turno de conversa. Roda o loop de tool-use e devolve
    `{resposta, acoes, mudou_estado, ia_indisponivel}`.

    `contexto` (data de hoje, seleção atual, etc.) entra como FATOS no início do
    pedido — o agente resolve "sexta"/"meu sábado" a partir daí, não inventa.
    `historico` é a conversa anterior (lista {role, content}, só texto).
    Levanta `AgenteIndisponivel` se o cérebro estiver fora/desligado.
    """
    if not settings.AGENTE_ENABLED:
        raise AgenteIndisponivel("agente desligado")

    pedido = (
        "FATOS (use só isto para resolver datas; não invente):\n"
        + json.dumps(contexto, ensure_ascii=False)
        + "\n\nPedido do usuário: "
        + mensagem
    )
    prov = _criar_provider(historico or [], pedido)

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
                saida = ferr["executar"](**tc.args)
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
