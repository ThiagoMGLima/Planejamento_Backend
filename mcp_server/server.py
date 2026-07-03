"""Servidor MCP do Planejador de Rotina (Marco C4).

Camada FINA de ferramentas sobre a API HTTP local (`web:8000`) — zero lógica
própria, zero import de Django. Torna o backend usável por QUALQUER runtime de
agente (Claude, Hermes, etc. — o framework do agente é a parte trocável; esta
camada de ferramentas é o investimento durável).

O que-if conversacional ("e se eu estudar Cálculo na quinta?") é
`simular_plano` com entradas hipotéticas — já suportado pela pureza do solver,
sem código novo no backend. Solver, diretrizes e dados continuam 100% locais.

Transporte: streamable-http em MCP_HOST:MCP_PORT (endpoint /mcp).
"""

import asyncio
import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "planejador-rotina",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8765")),
    instructions=(
        "Ferramentas do Planejador de Rotina (backend local). O solver é a "
        "fonte de verdade: todo plano exibido vem dele. `simular_plano` e "
        "`replanejar` (sem aplicar) são what-if — nada é persistido; "
        "`escolher_cenario` e `replanejar(aplicar=true)` persistem."
    ),
)


def _base_url():
    return os.environ.get("API_BASE_URL", "http://web:8000/api/v1")


async def _api(metodo, caminho, json=None, params=None):
    """Chama a API e devolve o corpo. Erro HTTP vira dict (o agente lê o motivo)."""
    async with httpx.AsyncClient(base_url=_base_url(), timeout=60.0) as cli:
        resp = await cli.request(metodo, caminho, json=json, params=params)
    try:
        corpo = resp.json()
    except ValueError:
        corpo = {"detalhe": resp.text}
    if resp.status_code >= 400:
        return {"erro": resp.status_code, "detalhe": corpo}
    return corpo


async def criar_tarefa(
    titulo: str,
    classe_id: str | None = None,
    deadline: str | None = None,
    esforco_min: int | None = None,
    descricao: str = "",
) -> dict:
    """Cria uma tarefa no Inbox. Para ela entrar num plano, precisa de
    deadline (ISO-8601 com offset), esforco_min (minutos) e classe_id."""
    corpo = {"titulo": titulo, "descricao": descricao}
    if classe_id is not None:
        corpo["classe_id"] = classe_id
    if deadline is not None:
        corpo["deadline"] = deadline
    if esforco_min is not None:
        corpo["esforco_estimado"] = esforco_min
    return await _api("POST", "/tarefas/", json=corpo)


async def listar_classes() -> list | dict:
    """Lista as classes de atividade (id, nome) — use o id em criar_tarefa."""
    resultado = await _api("GET", "/classes/")
    if isinstance(resultado, dict) and "results" in resultado:
        return resultado["results"]
    return resultado


async def listar_pendentes() -> list | dict:
    """Eventos rastreáveis já vencidos e não concluídos (status PENDENTE)."""
    return await _api("GET", "/pendentes")


async def simular_plano(
    tarefa_ids: list[str],
    preferencias: dict | None = None,
    horizonte: str | None = None,
    a_partir_de: str | None = None,
) -> dict:
    """What-if: monta um plano para as tarefas SEM persistir nada.

    horizonte: AUTOMATICO | SEMANA | DUAS_SEMANAS | MES."""
    corpo = {"tarefa_ids": tarefa_ids}
    if preferencias:
        corpo["preferencias"] = preferencias
    if horizonte:
        corpo["horizonte"] = horizonte
    if a_partir_de:
        corpo["a_partir_de"] = a_partir_de
    return await _api("POST", "/planejamento/calcular", json=corpo)


async def gerar_cenarios(
    tarefa_ids: list[str],
    preferencias: dict | None = None,
    horizonte: str | None = None,
) -> dict:
    """Gera 3–4 cenários comparáveis com métricas e trade-offs (assíncrono no
    backend; esta ferramenta encapsula o polling e devolve o resultado pronto,
    com o job_id necessário para escolher_cenario)."""
    corpo = {"tarefa_ids": tarefa_ids}
    if preferencias:
        corpo["preferencias"] = preferencias
    if horizonte:
        corpo["horizonte"] = horizonte
    resp = await _api("POST", "/planejamento/cenarios", json=corpo)
    if "erro" in resp:
        return resp
    if resp.get("status") == "pronto":  # cache hit
        return {"job_id": resp.get("job_id"), **resp["resultado"]}

    job_id = resp["job_id"]
    # Piso no intervalo: garante que `passado` avança (timeout sempre chega).
    intervalo = max(float(os.environ.get("MCP_POLL_INTERVALO_S", "5")), 0.01)
    timeout = float(os.environ.get("MCP_POLL_TIMEOUT_S", "300"))
    passado = 0.0
    while passado < timeout:
        status = await _api("GET", f"/planejamento/cenarios/{job_id}")
        if status.get("status") == "pronto":
            return {"job_id": job_id, **status["resultado"]}
        if status.get("status") == "erro":
            return {"erro": 500, "detalhe": status, "job_id": job_id}
        await asyncio.sleep(intervalo)
        passado += intervalo
    return {
        "erro": 504,
        "detalhe": "tempo esgotado aguardando os cenários",
        "job_id": job_id,
    }


async def escolher_cenario(job_id: str, cenario_id: str, aplicar: bool = False) -> dict:
    """Registra a escolha de um cenário (o sistema aprende os pesos) e, com
    aplicar=true, persiste o plano do cenário no calendário."""
    return await _api(
        "POST",
        "/planejamento/cenarios/escolher",
        json={"job_id": job_id, "cenario_id": cenario_id, "aplicar": aplicar},
    )


async def replanejar(
    dias_bloqueados: list[str] | None = None,
    preferencias: dict | None = None,
    aplicar: bool = False,
) -> dict:
    """Replaneja a agenda do agora em diante (emergências). aplicar=false é
    simulação (plano novo + diff, nada persistido); aplicar=true substitui as
    sessões futuras. "Hoje não" = dias_bloqueados=[data de hoje]."""
    corpo = {}
    if dias_bloqueados:
        corpo["dias_bloqueados"] = dias_bloqueados
    if preferencias:
        corpo["preferencias"] = preferencias
    caminho = (
        "/planejamento/replanejar/aplicar" if aplicar else "/planejamento/replanejar"
    )
    return await _api("POST", caminho, json=corpo)


async def remarcar(
    evento_id: str, escopo: str = "serie", data: str | None = None
) -> dict:
    """Remarca um evento: encerra a ocorrência/série e devolve a tarefa de
    origem ao Inbox. escopo=ocorrencia exige data (YYYY-MM-DD)."""
    params = {"escopo": escopo}
    if data:
        params["data"] = data
    return await _api("POST", f"/eventos/{evento_id}/remarcar/", params=params)


# Registro explícito (em vez de decorator): mantém os símbolos do módulo como
# funções puras — os testes de contrato as chamam direto, com o HTTP mockado.
for _fn in (
    criar_tarefa,
    listar_classes,
    listar_pendentes,
    simular_plano,
    gerar_cenarios,
    escolher_cenario,
    replanejar,
    remarcar,
):
    mcp.tool()(_fn)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
