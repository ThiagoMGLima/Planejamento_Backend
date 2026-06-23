# Implementação — Planejamento assistido por IA (guia para agente)

> **Companheiro de `planejamento-ia-analise.md`** (o *porquê*/decisões). Este doc é
> o *como*: ordem de execução, assinaturas, trechos de código nos pontos delicados,
> contratos e testes. Um agente deve conseguir implementar seguindo na ordem.
>
> **Leia antes:** `planejamento-ia-analise.md`, `services/planejamento.py`,
> `views.py` (`planejamento_calcular`/`planejamento_aplicar`), `serializers.py`
> (`CalcularSerializer`/`PreferenciasSerializer`), `config/celery.py`, `config/settings.py`.

## 0. Princípios invioláveis
1. **Não quebrar** `/calcular` nem `/aplicar` (contratos atuais idênticos).
2. **Sem migração** — nenhuma mudança de modelo.
3. **O solver é o guardião:** o plano entregue é sempre saída do solver
   (válido por construção). A IA só **influencia entradas** (diretrizes) e **narra**.
4. **Grounding no código:** números/datas vêm do código; a IA não calcula.
5. **IA é opcional:** se o Ollama falhar/desligado, entrega o **plano base** + flag.
6. Estilo: ruff + black limpos; PT-BR nos comentários; casar com o código existente.

---

## Passo 1 — Estender o solver (`services/planejamento.py`)

Adicionar 3 knobs **por tarefa** (opcionais; defaults preservam o comportamento atual).

### 1.1 `TarefaEntrada` ganha campos
```python
@dataclass
class TarefaEntrada:
    id: str
    titulo: str
    classe_id: str
    esforco: int
    deadline: datetime
    prioridade: int | None = None     # 1..5 (None ⇒ neutro = 3)
    buffer_dias: int = 0              # terminar N dias antes da deadline
    max_min_por_dia: int | None = None  # teto diário específico desta tarefa
```

### 1.2 Deadline efetiva (buffer) — helper novo
```python
def _deadline_efetiva(tarefa, agora):
    """Deadline antecipada pelo buffer; se o buffer a jogar pro passado, ignora."""
    if not tarefa.buffer_dias:
        return tarefa.deadline
    efetiva = tarefa.deadline - timedelta(days=tarefa.buffer_dias)
    return efetiva if efetiva > agora else tarefa.deadline
```
Em `calcular_plano`, **trocar `tarefa.deadline` por `_deadline_efetiva(tarefa, agora)`**
em: (a) ordenação (folga), (b) checagem `deadline <= agora`, (c) `fim_busca = min(...)`.

### 1.3 Ordenação EDF com prioridade (desempate)
```python
tarefas_ord = sorted(
    tarefas,
    key=lambda t: (
        _deadline_efetiva(t, agora),                 # 1º: prazo manda (factibilidade)
        -(t.prioridade or 3),                        # 2º: maior prioridade primeiro
        (_deadline_efetiva(t, agora) - agora) - timedelta(minutes=t.esforco),  # menor folga
        -t.esforco,
    ),
)
```
> **Limitação documentada (v1):** prioridade é **desempate** — o prazo continua
> dominando (EDF garante factibilidade). "Proteger tarefa importante de ser
> espremida" é objetivo de otimização para v1.1.

### 1.4 Teto diário por tarefa em `_alocar`
Substituir o bloco do teto por:
```python
cap_tarefa = pn.max_dia_tarefa  # já reflete relaxamento (None nos níveis >= 2)
if cap_tarefa is not None and tarefa.max_min_por_dia is not None:
    cap_tarefa = min(cap_tarefa, tarefa.max_min_por_dia)
if cap_tarefa is not None:
    limites.append(cap_tarefa - min_tarefa_dia.get((tarefa.id, dia), 0))
```
> Override é **suave**: quando o relaxamento zera o teto global (nível ≥ 2), o
> override também é ignorado (factibilidade vence).

### 1.5 Testes do Passo 1 (em `tests/test_planejamento.py`)
- `prioridade` altera a ordem **só em empate de deadline** (duas tarefas mesmo
  deadline → a de maior prioridade pega o slot mais cedo).
- `buffer_dias=2` ⇒ todas as sessões terminam ≤ `deadline - 2d`.
- `buffer_dias` impossível (jogaria pro passado) ⇒ usa a deadline real (não some).
- `max_min_por_dia` por tarefa ⇒ nenhum dia daquela tarefa excede o valor (quando cabe).
- **Invariantes preservadas** com knobs: soma = esforço, sem sobreposição.

---

## Passo 2 — Refactor de orquestração (`services/planejamento.py` + `views.py`)

Extrair da view o que será reusado pela task. **Sem mudar o contrato do `/calcular`.**

### 2.1 `validar_tarefas` (a lógica do 422 de hoje)
```python
def validar_tarefas(tarefa_ids):
    """Retorna (validas: list[Tarefa], invalidas: list[dict{tarefa_id, motivo}])."""
    # mesma regra do planejamento_calcular atual:
    # inexistente / PROMOVIDA / faltando deadline|esforco_estimado|classe
```

### 2.2 `montar_plano` (orquestra o solver; aceita diretrizes)
```python
@dataclass
class ResultadoPlano:
    sessoes: list[Sessao]
    nao_alocado: list[NaoAlocado]
    prefs: Preferencias
    prefs_usadas: dict
    tarefas: list[TarefaEntrada]
    ocupado: list
    agora: datetime
    horizonte_fim: datetime

def montar_plano(tarefas_validas, agora, preferencias_entrada, diretrizes=None):
    """Monta TarefaEntrada (aplicando `diretrizes` se houver), calcula `ocupado`,
    define horizonte (min(max(deadline_efetiva), agora + JANELA_MAX)) e roda
    `calcular_plano`. Retorna ResultadoPlano."""
```
- `diretrizes` é um dict já **validado** (ver Passo 4.3): `{prioridades:{id:int},
  ajustes_por_tarefa:{id:{buffer_dias,max_min_por_dia}}}`. Mapear pros campos de
  `TarefaEntrada`.
- **Mover `JANELA_MAX`** de `views.py` para `services/planejamento.py` (a view importa de lá).

### 2.3 `/calcular` passa a usar os dois
```python
validas, invalidas = planejamento.validar_tarefas(ids)
if invalidas: return Response({"tarefas_invalidas": invalidas}, status=422)
agora = dados.get("a_partir_de") or timezone.now()
prefs, prefs_usadas = planejamento.montar_preferencias(dados.get("preferencias", {}))
res = planejamento.montar_plano(validas, agora, dados.get("preferencias", {}))
return Response(_serializar_plano(res))  # mesmo shape de hoje
```
Criar `_serializar_plano(res) -> {sessoes, nao_alocado, preferencias_usadas}` (helper
compartilhado; o formato de `sessoes`/`nao_alocado` é o atual).

### 2.4 Testes do Passo 2
- **Os 7 testes de API atuais de `/calcular` continuam passando sem alteração.**
- `validar_tarefas` retorna as invalidas certas (inexistente/promovida/faltando).

---

## Passo 3 — Ollama no compose + config (CPU primeiro)

### 3.1 `docker-compose.yml` (versão CPU)
```yaml
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_models:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "ps"]
      interval: 10s
      timeout: 5s
      retries: 5
# em volumes: adicionar  ollama_models: {}
```
- `web` e `celery` ganham `depends_on: ollama` (sem `condition` rígida pra não travar).
- **Baixar o modelo** (uma vez): `docker compose exec ollama ollama pull qwen2.5:7b-instruct`.

### 3.2 `requirements.txt`
Adicionar `ollama` (cliente oficial Python).

### 3.3 `config/settings.py` (lendo de env, com defaults)
```python
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
IA_PLANEJAMENTO_ENABLED = os.getenv("IA_PLANEJAMENTO_ENABLED", "1") == "1"
```
Acrescentar as 4 chaves no `.env.example`.

### 3.4 Celery result backend — **já pronto, só confirmar**
`config/settings.py` já tem `CELERY_RESULT_BACKEND` (Redis) e
`CELERY_TASK_TRACK_STARTED=True`; `config/celery.py` já faz `autodiscover_tasks()`
(acha `planner/tasks.py`). `AsyncResult` funciona sem mudança. Nada a fazer aqui.

---

## Passo 4 — `services/planejamento_ia.py` (novo)

### 4.1 `construir_contexto(res: ResultadoPlano) -> dict`
Fatos **já calculados** (a IA não recomputa):
```python
{
  "agora": "ISO",
  "tarefas": [
    {"id","titulo","classe","deadline":"ISO","esforco_min",
     "alocado_min","restante_min","sessoes":N,"dias_usados":[...]}
  ],
  "carga_por_dia": {"2026-06-22": 120, ...},
  "capacidade_livre_antes_da_deadline": {"<id>": minutos_uteis_restantes},
  "nao_alocado": [{"id","titulo","restante_min","motivo"}],
  "preferencias": {...}
}
```
`restante_min` vem do `nao_alocado`; `capacidade_livre` = soma dos slots livres
(reusar `slots_livres`) até a deadline de cada tarefa.

### 4.2 `gerar_melhoria(contexto) -> dict` (1 chamada Ollama)
```python
import json, ollama
from django.conf import settings

class OllamaIndisponivel(Exception): ...

SCHEMA_MELHORIA = { ... }  # JSON Schema do §4 do design (diretrizes, resumo, trade_offs, sugestoes)

def gerar_melhoria(contexto):
    try:
        cli = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT)
        resp = cli.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": json.dumps(contexto, ensure_ascii=False)}],
            format=SCHEMA_MELHORIA,
            options={"temperature": 0},
        )
        return json.loads(resp["message"]["content"])
    except Exception as e:           # rede, timeout, JSON inválido
        raise OllamaIndisponivel(str(e))
```
`SYSTEM_PROMPT` = o do §5 do design (tom objetivo, "nunca invente números").

### 4.3 `validar_diretrizes(bruto, tarefas_validas) -> dict`
Guarda-corpo (descarta o inválido, nunca lança):
- `prioridades[id]`: só ids existentes; inteiro **clamp 1..5**.
- `ajustes_por_tarefa[id]`: id existente; `buffer_dias` int ≥ 0 (clamp p/ ≤ horizonte);
  `max_min_por_dia` int ≥ 1 (reusar limites do `PreferenciasSerializer`).
- Retorna o dict limpo (chaves desconhecidas removidas).

### 4.4 `alertas_do_plano(res_melhorado: ResultadoPlano) -> list[dict]` (CÓDIGO, sem IA)
```python
# nao_alocado → severidade "alto": "Faltam {restante_min} min de {titulo} antes do prazo."
# dia com carga > teto_total (se houver) → "medio"
# retorna [{tarefa_id, severidade, mensagem}]
```

### 4.5 Testes do Passo 4 (mock do Ollama)
- `construir_contexto`: `restante_min`, `carga_por_dia`, `capacidade_livre` corretos.
- `validar_diretrizes`: descarta id inexistente, clampa prioridade fora de 1..5,
  rejeita `max_min_por_dia` inválido.
- `gerar_melhoria`: com `ollama.Client` **mockado** retornando JSON fixo → parse ok;
  exceção do cliente → levanta `OllamaIndisponivel`.
- `alertas_do_plano`: gera alerta "alto" para item em `nao_alocado`.

---

## Passo 5 — Celery task + cache (`planner/tasks.py` novo ou em services)

```python
import hashlib, json
from celery import shared_task
from django.core.cache import cache
from django.utils.dateparse import parse_datetime
from . import ... (planejamento, planejamento_ia)

def _chave_cache(tarefa_ids, prefs_usadas, sessoes_base):
    base = json.dumps({
        "ids": sorted(map(str, tarefa_ids)),
        "prefs": prefs_usadas,
        "plano": [(s["tarefa_id"], s["inicio"], s["fim"]) for s in sessoes_base],
    }, sort_keys=True, ensure_ascii=False)
    return "planejar_ia:" + hashlib.sha256(base.encode()).hexdigest()

@shared_task
def planejar_ia_task(tarefa_ids, a_partir_de_iso, preferencias):
    agora = parse_datetime(a_partir_de_iso)
    validas, _ = planejamento.validar_tarefas(tarefa_ids)
    base = planejamento.montar_plano(validas, agora, preferencias)
    plano_base = _serializar_plano(base)
    chave = _chave_cache(tarefa_ids, plano_base["preferencias_usadas"], plano_base["sessoes"])
    if (hit := cache.get(chave)) is not None:
        return hit
    try:
        if not settings.IA_PLANEJAMENTO_ENABLED:
            raise planejamento_ia.OllamaIndisponivel("desligado")
        contexto = planejamento_ia.construir_contexto(base)
        bruto = planejamento_ia.gerar_melhoria(contexto)
        diretrizes = planejamento_ia.validar_diretrizes(bruto.get("diretrizes", {}), base.tarefas)
        melhor = planejamento.montar_plano(validas, agora, preferencias, diretrizes)
        resultado = {
            "plano": _serializar_plano(melhor),
            "resumo": bruto.get("resumo", ""),
            "trade_offs": bruto.get("trade_offs", []),
            "alertas": planejamento_ia.alertas_do_plano(melhor),
            "sugestoes": bruto.get("sugestoes", []),
            "ia_indisponivel": False,
        }
    except planejamento_ia.OllamaIndisponivel:
        resultado = {
            "plano": plano_base, "resumo": "", "trade_offs": [],
            "alertas": planejamento_ia.alertas_do_plano(base),
            "sugestoes": [], "ia_indisponivel": True,
        }
    cache.set(chave, resultado, timeout=3600)
    return resultado
```
> `_serializar_plano` é o helper compartilhado do Passo 2.3 (mover para o módulo
> de services se a task precisar importar — evitar import circular com `views`).

---

## Passo 6 — Endpoints + serializers + urls

### 6.1 `views.py`
```python
@api_view(["POST"]); @permission_classes([AllowAny])
def planejamento_planejar_ia(request):
    # 1. CalcularSerializer (400 vazio) → ids
    # 2. validar_tarefas → 422 se invalidas
    # 3. agora = a_partir_de or now(); base = montar_plano(...); plano_base = _serializar_plano(base)
    # 4. chave = _chave_cache(...); if hit: return 200 {"status":"pronto","resultado":hit}
    # 5. task = planejar_ia_task.delay(ids_str, agora.isoformat(), prefs_entrada)
    # 6. return 202 {"job_id": task.id, "status":"processando"}

@api_view(["GET"]); @permission_classes([AllowAny])
def planejamento_planejar_ia_status(request, job_id):
    r = AsyncResult(job_id)
    if r.successful(): return Response({"status":"pronto","resultado": r.result})
    if r.failed():     return Response({"status":"erro","detalhe":"falha no processamento"})
    return Response({"status":"processando"})
```

### 6.2 `urls.py` (rotas sem barra, padrão da casa)
```python
path("planejamento/planejar-ia", views.planejamento_planejar_ia, name="planejar-ia"),
path("planejamento/planejar-ia/<uuid:job_id>", views.planejamento_planejar_ia_status, name="planejar-ia-status"),
```
> `job_id` do Celery é UUID; usar `<uuid:job_id>` ou `<str:job_id>` (confirmar formato).

### 6.3 Sem serializer novo de saída — montamos dicts (igual ao `/calcular`).

---

## Passo 7 — Testes (consolidado; Ollama sempre mockado)

Em `tests/` (pode ser `test_planejamento_ia.py`):
- **Solver estendido** (Passo 1.5) — unit, puro.
- **Pipeline feliz**: mock `gerar_melhoria` → diretrizes válidas → resposta tem
  `plano`, `resumo`, `trade_offs`, `alertas`, `sugestoes`, `ia_indisponivel=False`;
  plano **sem sobreposição** e soma correta.
- **Fallback**: `gerar_melhoria` levanta `OllamaIndisponivel` → resposta = plano
  base + `ia_indisponivel=True` + alertas (não quebra).
- **Cache**: 2ª chamada idêntica não chama `gerar_melhoria` (assert no mock).
- **Endpoints** (Celery `task_always_eager=True` nos testes): POST → 200 com
  resultado (eager) ou 202; 422 tarefa inválida; 400 vazio.
- **`/calcular` e `/aplicar` intactos** (suíte atual verde).

> Em `settings` de teste / fixture: `CELERY_TASK_ALWAYS_EAGER=True` e cache locmem.

---

## Passo 8 — Atualizar a task do front
Reescrever `frontend/Planejador_Frontend/tasks/planejamento-ia-analise-frontend.md`
para o **novo contrato**: o plano agora vem do `planejar-ia` assíncrono (não mais
`/calcular` + comentário sobreposto). Endpoints, shapes e o "Aplicar sugestão"
(que re-chama `planejar-ia`) atualizados.

---

## Critérios de aceite
- `ruff check .` ✓ · `black --check .` ✓ · `makemigrations --check` (sem mudanças) ✓.
- `pytest` verde, incluindo os novos e **os atuais sem regressão**.
- `/calcular` e `/aplicar` com respostas **idênticas** às de hoje.
- `planejar-ia` (com Ollama mockado/eager) devolve o contrato do §"Contrato final".
- Fallback funciona com `IA_PLANEJAMENTO_ENABLED=0`.
- Plano entregue **sempre válido** (sem sobreposição, soma = esforço) — inclusive o melhorado.

## Contrato final (resumo)

**`POST /api/v1/planejamento/planejar-ia`** — body igual ao `/calcular`.
→ `202 {job_id, status:"processando"}` ou `200 {status:"pronto", resultado:{...}}` (cache hit).
422 tarefa inválida · 400 vazio.

**`GET /api/v1/planejamento/planejar-ia/{job_id}`**
→ `{status:"processando"}` | `{status:"erro",detalhe}` | `{status:"pronto", resultado}`.

**`resultado`:**
```json
{
  "plano": { "sessoes": [...], "nao_alocado": [...], "preferencias_usadas": {...} },
  "resumo": "string objetiva",
  "trade_offs": ["..."],
  "alertas": [ {"tarefa_id","severidade","mensagem"} ],
  "sugestoes": [ {"tipo","descricao","acao":{"preferencias":{...}}} ],
  "ia_indisponivel": false
}
```
`plano.sessoes` tem o mesmo shape do `/calcular`; o `/aplicar` consome igual.

## Ordem de execução para o agente
1 → 2 (refactor; manter suíte verde) → 3 (infra Ollama, CPU) → 4 (IA service, mock)
→ 5 (task+cache) → 6 (endpoints) → 7 (testes) → 8 (task front). Commit por passo
ou um PR único `feat: planejamento assistido por IA (fase A)`.
