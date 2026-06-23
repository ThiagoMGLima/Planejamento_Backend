"""Sincronização one-way Notion → Inbox (captura no celular).

A tarefa é anotada numa database do Notion (tipada: Tarefa, Prazo, Esforço,
Classe, Status). Este módulo PUXA as páginas com `Status = Nova`, cria as
`Tarefa` correspondentes na Inbox e marca a página como `Importada`. É a única
escrita que fazemos no Notion; de resto, fluxo one-way.

Idempotência: cada página vira no máximo uma `Tarefa` (via `origem_externa_id`).
Reexecutar é seguro — páginas já importadas são puladas (e re-marcadas se o flip
de status tiver falhado antes).

O backend só CHAMA o Notion (nunca fica exposto). Sem token/database
configurados, `sincronizar` levanta `NotionDesligado` (o caller responde 400).
"""

from datetime import datetime

import requests
from django.conf import settings
from django.utils import timezone

from ..models import Classe, Tarefa

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TIMEOUT_S = 15

# Nomes EXATOS das propriedades esperadas na database do Notion (ver docs).
PROP_TITULO = "Tarefa"
PROP_PRAZO = "Prazo"
PROP_ESFORCO = "Esforço (min)"
PROP_CLASSE = "Classe"
PROP_STATUS = "Status"
STATUS_NOVA = "Nova"
STATUS_IMPORTADA = "Importada"


class NotionDesligado(Exception):
    """Token/database não configurados — integração desligada."""


class NotionIndisponivel(Exception):
    """Falha de rede/API ao falar com o Notion (não foi possível sincronizar)."""


def _headers():
    return {
        "Authorization": f"Bearer {settings.NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _hora_padrao():
    """Hora local (time) usada quando o Prazo vem só como data."""
    hh, mm = (int(p) for p in settings.NOTION_DEADLINE_HORA_PADRAO.split(":"))
    return hh, mm


def _buscar_paginas_novas():
    """Todas as páginas com Status = Nova (segue a paginação do Notion)."""
    url = f"{NOTION_API}/databases/{settings.NOTION_DATABASE_ID}/query"
    corpo = {
        "filter": {"property": PROP_STATUS, "select": {"equals": STATUS_NOVA}},
        "page_size": 100,
    }
    paginas = []
    cursor = None
    try:
        while True:
            if cursor:
                corpo["start_cursor"] = cursor
            resp = requests.post(url, headers=_headers(), json=corpo, timeout=TIMEOUT_S)
            if resp.status_code != 200:
                raise NotionIndisponivel(f"query {resp.status_code}: {resp.text[:200]}")
            dados = resp.json()
            paginas.extend(dados.get("results", []))
            if not dados.get("has_more"):
                return paginas
            cursor = dados.get("next_cursor")
    except requests.RequestException as e:
        raise NotionIndisponivel(str(e))


def _texto_titulo(prop):
    partes = (prop or {}).get("title", []) if prop else []
    return "".join(p.get("plain_text", "") for p in partes).strip()


def _para_deadline(prop):
    """Date do Notion → datetime tz-aware. Data pura cai na hora padrão."""
    data = (prop or {}).get("date") if prop else None
    inicio = (data or {}).get("start")
    if not inicio:
        return None
    tz = timezone.get_current_timezone()
    if "T" in inicio:  # tem hora → ISO completo
        dt = datetime.fromisoformat(inicio)
        return dt if timezone.is_aware(dt) else timezone.make_aware(dt, tz)
    # só data (YYYY-MM-DD) → combina com a hora padrão no fuso local
    hh, mm = _hora_padrao()
    dia = datetime.fromisoformat(inicio).replace(hour=hh, minute=mm)
    return timezone.make_aware(dia, tz)


def _extrair(page):
    """Página do Notion → dict cru (sem tocar no banco)."""
    props = page.get("properties", {})
    classe_sel = (props.get(PROP_CLASSE) or {}).get("select")
    return {
        "page_id": page["id"],
        "titulo": _texto_titulo(props.get(PROP_TITULO)),
        "deadline": _para_deadline(props.get(PROP_PRAZO)),
        "esforco": (props.get(PROP_ESFORCO) or {}).get("number"),
        "classe_nome": classe_sel.get("name") if classe_sel else None,
    }


def _marcar_importada(page_id):
    """Flip de Status → Importada (idempotente; erro vira NotionIndisponivel)."""
    url = f"{NOTION_API}/pages/{page_id}"
    corpo = {"properties": {PROP_STATUS: {"select": {"name": STATUS_IMPORTADA}}}}
    try:
        resp = requests.patch(url, headers=_headers(), json=corpo, timeout=TIMEOUT_S)
        if resp.status_code != 200:
            raise NotionIndisponivel(f"patch {resp.status_code}: {resp.text[:200]}")
    except requests.RequestException as e:
        raise NotionIndisponivel(str(e))


def sincronizar():
    """Importa as páginas novas. Retorna {importadas, ignoradas, erros}.

    Erros por página (ex.: título vazio) são coletados sem abortar o lote; falhas
    de rede/API do Notion levantam `NotionIndisponivel`.
    """
    if not settings.NOTION_TOKEN or not settings.NOTION_DATABASE_ID:
        raise NotionDesligado("NOTION_TOKEN/NOTION_DATABASE_ID não configurados.")

    classes = {c.nome: c for c in Classe.objects.all()}
    importadas, ignoradas, erros = 0, 0, []

    for page in _buscar_paginas_novas():
        item = _extrair(page)
        pid = item["page_id"]

        # Já importada antes: re-marca (auto-cura flip que falhou) e ignora.
        if Tarefa.objects.filter(origem_externa_id=pid).exists():
            _marcar_importada(pid)
            ignoradas += 1
            continue

        if not item["titulo"]:
            erros.append({"page_id": pid, "motivo": "título vazio"})
            continue

        Tarefa.objects.create(
            titulo=item["titulo"],
            deadline=item["deadline"],
            esforco_estimado=item["esforco"],
            classe=classes.get(item["classe_nome"]),
            status=Tarefa.Status.INBOX,
            origem_externa_id=pid,
        )
        _marcar_importada(pid)
        importadas += 1

    return {"importadas": importadas, "ignoradas": ignoradas, "erros": erros}
