# Plano de implementação — Rotina inteligente (marcos C1–C4)

> **Status: plano aprovado para execução.** Materializa a visão de
> `visao-rotina-inteligente.md` em marcos executáveis, no padrão do projeto
> (**1 marco = 1 PR**; aqui C1 se divide em dois PRs por tamanho). Cada marco
> lista arquivos, assinaturas, contratos de API, migrations e testes. Numa
> sessão futura basta pedir: *"Leia docs/tasks/rotina-inteligente-implementacao.md
> e execute o Marco C1a"*.
>
> Invariantes que NENHUM marco pode violar:
> 1. **Solver é a fonte de verdade** — todo plano exibido saiu de `montar_plano`;
>    a IA só propõe diretrizes, validadas por guarda-corpo que nunca levanta.
> 2. **Números vêm do código** (métricas, diffs, alertas) — nunca da IA.
> 3. **Views finas** → lógica em `planner/services/`; services nunca importam views.
> 4. **Degradação elegante** — Ollama fora ⇒ resposta útil + `ia_indisponivel: true`.
> 5. **Persistir é do endpoint de aplicar** — funções de plano continuam puras.

## Visão geral dos PRs

| PR  | Marco | Entrega                                                        |
| --- | ----- | -------------------------------------------------------------- |
| 1   | C1a   | Vocabulário novo do solver (janela por dia, fds, dias bloqueados) |
| 2   | C1b   | Pipeline de cenários + score/pesos + `EscolhaCenario`          |
| 3   | C2    | Replanejar a partir de agora + diff                            |
| 4   | C3    | `RegistroExecucao` + fatores adaptativos no solver/contexto    |
| 5   | C4    | Servidor MCP sobre a API (+ agente configurável)               |

Dependências: C1a → C1b → (C2, C3 em qualquer ordem) → C4.

---

## Marco C1a — Vocabulário do solver (PR 1)

Hoje as diretrizes só apertam. O cenário "trabalhe até 20h na quinta e ganhe o
sábado" exige três alavancas novas, todas **suaves** (a cascata de relaxamento
continua garantindo os prazos).

### A1. `Preferencias` (`services/planejamento.py`)

Três campos novos, com defaults neutros (comportamento atual intocado):

```python
@dataclass
class Preferencias:
    ...  # campos atuais
    janela_por_dia: dict | None = None   # chave "0".."6" (dia da semana) ou
                                          # "YYYY-MM-DD" (data; vence o dia da
                                          # semana) → (inicio_min, fim_min)
    usar_fds: bool | None = None          # True força liberar fds já no nível 0
                                          # (None ⇒ segue evitar_fds como hoje)
    dias_bloqueados: frozenset = frozenset()  # datas sem NENHUMA sessão
```

### A2. Resolução da janela por dia (`slots_livres`)

Extrair um helper e usá-lo no loop de `slots_livres`:

```python
def _janela_do_dia(dia, pn, prefs):
    """(ini_min, fim_min) do dia ou None (dia fora do plano).

    Precedência: bloqueio > override por data > override por dia-da-semana >
    janela global do nível. Nos níveis ≥ 3 (24h) os overrides são ignorados —
    o relaxamento final continua soberano.
    """
```

Regras de interação com a cascata (`_prefs_do_nivel` + novo nível):

| Alavanca          | Níveis 0–2                     | Nível 3 (24h) | Nível 5 (novo)     |
| ----------------- | ------------------------------ | ------------- | ------------------- |
| `janela_por_dia`  | aplica (vence a global)        | ignorada      | ignorada            |
| `usar_fds=True`   | fds liberado desde o nível 0   | —             | —                   |
| `dias_bloqueados` | dia devolve None (bloqueado)   | ainda bloqueado | **liberado**      |

`NIVEIS = (0, 1, 2, 3, 4, 5)` — o nível 5 só libera `dias_bloqueados`; é o
último recurso antes de `nao_alocado` (prazo continua dominando, mas o bloqueio
é a preferência mais "dura" da hierarquia). Sem diretrizes novas, o nível 5 é
idêntico ao 4 (zero mudança de comportamento).

### A3. `montar_plano` + `validar_diretrizes`

- `montar_plano`: diretrizes ganham as chaves `janela_por_dia`, `usar_fds`,
  `dias_bloqueados`; aplicadas via `replace(prefs, ...)` como o teto total hoje.
- `validar_diretrizes` (`services/planejamento_ia.py`) — clamp/descarte, nunca levanta:
  - `janela_por_dia`: chave dia-da-semana `"0".."6"` ou data ISO dentro do
    horizonte; valor `["HH:MM","HH:MM"]` com `05:00 ≤ ini < fim ≤ 23:59`;
    inválido ⇒ entrada descartada.
  - `usar_fds`: coerção para bool; qualquer outra coisa ⇒ descartado.
  - `dias_bloqueados`: lista de datas ISO dentro do horizonte, dedup, máx. 14;
    excedente descartado. (Não valida factibilidade — o nível 5 resolve.)
- **Semântica de cenário**: dentro de um cenário, encolher/estender a janela do
  usuário é permitido (proposta explícita que ele verá e aceitará) — diferente
  do teto total do planejar-ia, que só aperta.

### A4. Testes (`planner/tests/test_planejamento.py` + novos)

- `janela_por_dia` por dia-da-semana estende quinta até 20h (sessão às 19h só na quinta).
- Override por data vence o de dia-da-semana.
- `dias_bloqueados` fica vazio nos níveis 0–4 e é usado no 5 quando é a única forma de cumprir o prazo.
- `usar_fds=True` aloca sábado no nível 0.
- Sem diretrizes novas ⇒ plano byte-idêntico ao atual (regressão).
- `validar_diretrizes`: cada regra de clamp/descarte acima.

---

## Marco C1b — Pipeline de cenários (PR 2)

### B1. Novo service `planner/services/cenarios.py`

```python
# Arquétipos por CÓDIGO (sempre presentes; degradação sem IA).
# Cada um é fn(contexto) -> dict diretrizes, derivando valores dos fatos:
ARQUETIPOS = {
    "base":             lambda ctx: {},
    "espalhado":        _espalhado,        # teto total ≈ carga_media_dia_min
    "intenso":          _intenso,          # janela +2h seg–sex; sáb+dom bloqueados
    "frente_carregada": _frente_carregada, # buffer_dias=2 nas 3 deadlines mais próximas
}

def gerar_cenarios_ia(contexto):
    """UMA chamada Ollama (SCHEMA_CENARIOS: array de 3–6 candidatos, cada um
    {nome, intencao, diretrizes}) — mesmo padrão de gerar_melhoria; falha ⇒
    OllamaIndisponivel (caller degrada p/ só arquétipos). temperature 0."""

def metricas_do_plano(res):
    """Por CÓDIGO: pico_min_dia, dias_livres, fds_livres, folga_media_h,
    min_fora_janela (vs janela do USUÁRIO), fragmentacao (sessões/tarefa),
    nao_alocado_min."""

def normalizar(m, m_base):
    """Métricas relativas ao base do lote; custo→benefício invertido para
    'maior = melhor' em todas (pesos comparáveis)."""

def filtrar_dominados(cenarios):
    """Remove dominado (pior-ou-igual em TODAS as métricas que outro) e planos
    duplicados. `nao_alocado_min` pior que o base ⇒ eliminado direto."""

def pontuar(cenarios, pesos):
    """score = Σ peso_m × métrica_normalizada_m. Retorna ordenado; marca
    `sugerido` no maior score. Diversidade: o retorno final (máx. 4) SEMPRE
    inclui o base e ≥1 'contrariante' (melhor na métrica de menor peso)."""

def narrar(cenario, base):
    """Trade-offs por template de código sobre o diff de métricas
    ('sábado livre', 'pico cai de 6h para 4h'). Polimento por IA: adiável."""
```

### B2. Models + migration

```python
class PesoPreferencia(TimestampedModel):
    metrica = models.CharField(max_length=40, unique=True)
    valor = models.FloatField(default=1.0)          # neutro

class EscolhaCenario(TimestampedModel):
    lote = models.JSONField()          # todos os cenários exibidos + métricas
    escolhido = models.CharField(max_length=60)     # nome do cenário
    era_sugerido = models.BooleanField()
    pesos_no_momento = models.JSONField()           # auditoria/replay
```

Gravar a escolha **crua** permite trocar a regra de aprendizado e recalcular os
pesos do zero. Atualização (em `services/adaptacao.py`, criado aqui, cresce no C3):

```python
def atualizar_pesos(escolha):
    """EWMA por métrica: w ← clamp(w + ALFA·Δ, 0.2, 3.0), onde Δ = métrica
    normalizada do escolhido − média dos rejeitados. ALFA = 0.1.
    Decaimento p/ 1.0 (gostos mudam) entra no C3."""
```

### B3. API + task

```
POST /planejamento/cenarios
  body: {tarefa_ids, preferencias?, horizonte?}         (validação = planejar-ia)
  → 202 {job_id, tempo_estimado_s} | 200 (cache) | 400/422

GET  /planejamento/cenarios/{job_id}
  → 200 {status: "PROCESSANDO"} |
    200 {status: "PRONTO", cenarios: [{id, nome, intencao, sugerido, score,
         plano: {sessoes, nao_alocado}, metricas, metricas_vs_base,
         trade_offs: [str]}], pesos_usados, ia_indisponivel?}

POST /planejamento/cenarios/escolher
  body: {job_id, cenario_id, aplicar: bool}
  → grava EscolhaCenario + atualizar_pesos; se aplicar=true, persiste o plano
    reusando o serviço do /aplicar (transação); → 200 {aplicado, eventos_criados?}
```

- Task Celery `gerar_cenarios_task` (2º job real em `planner/tasks.py`): plano
  base → contexto → arquétipos + IA → guarda-corpo → solver N× → métricas →
  dominância → score → top 4. Cache Redis: chave
  `(tarefa_ids, prefs efetivas, horizonte, hash do plano base)`, TTL igual ao planejar-ia.
- `estimar_tempo_s` reusado (1 chamada de IA — mesma ordem de grandeza).

### B4. Testes (`test_cenarios.py`)

- Arquétipos derivam valores do contexto (espalhado usa a carga média real).
- Dominância: cenário pior em tudo some; duplicado some; pior que o base em `nao_alocado_min` some.
- Score ordena; sugerido = maior; base e contrariante sempre no retorno.
- `atualizar_pesos`: move na direção certa, respeita clamp, α pequeno.
- Endpoint: 202→polling→PRONTO; Ollama fora ⇒ só arquétipos + `ia_indisponivel`.
- `escolher` grava `EscolhaCenario` (com lote e pesos), atualiza pesos, aplica quando pedido.
- IA proponente ruim (ids inexistentes, janelas absurdas) ⇒ guarda-corpo limpa, pipeline não quebra.

---

## Marco C2 — Replanejar a partir de agora (PR 3)

Sem IA; síncrono (ms). Novo service `planner/services/replanejamento.py`.

### R1. Serviço

```python
def replanejar(agora, dias_bloqueados=None, preferencias=None):
    """1. sessões futuras aplicadas: Evento.objects.filter(
          origem_tarefa__isnull=False, inicio__gte=agora, status≠CONCLUIDO)
       2. esforço restante por tarefa = Σ minutos dessas sessões
          (+ tarefas de volta no Inbox via `remarcar`, elegíveis como sempre)
       3. ocupado = intervalos_ocupados EXCLUINDO as sessões do passo 1
          (elas serão substituídas — não podem se auto-bloquear)
       4. plano novo = montar_plano(..., diretrizes={"dias_bloqueados": ...})
       5. diff = diff_planos(antigas, novas)
       PURO — não persiste. Retorna (ResultadoPlano, diff)."""

def diff_planos(sessoes_antigas, sessoes_novas):
    """Por tarefa: movidas [(de, para)], criadas, removidas, inalteradas —
    alimenta a narrativa 'Cálculo: qua→qui; sábado ganhou 1h' e o front."""
```

### R2. API

```
POST /planejamento/replanejar
  body: {dias_bloqueados?: ["YYYY-MM-DD"], preferencias?}
  → 200 {plano, diff, metricas, metricas_vs_anterior}     (nada persistido)

POST /planejamento/replanejar/aplicar
  body: idem
  → transação: recalcula, remove as sessões futuras substituídas, cria as
    novas (origem_tarefa preservado) → 200 {diff, eventos_criados, eventos_removidos}
```

Recalcular dentro do aplicar (em vez de confiar num plano enviado pelo cliente)
evita aplicar plano obsoleto; o corpo é o mesmo da simulação. "Hoje não" =
`dias_bloqueados=[hoje]` — sem endpoint próprio.

### R3. Testes

- Sessões passadas/concluídas intocadas; só futuras substituídas.
- Esforço restante bate com a soma das sessões substituídas.
- Sessões substituídas não se auto-bloqueiam no `ocupado`.
- `dias_bloqueados=[hoje]` esvazia hoje e o diff explica o custo.
- Diff: movida/criada/removida/inalterada cobertos.
- Aplicar é atômico e idempotente para o mesmo estado.

---

## Marco C3 — Registro de execução + fatores adaptativos (PR 4)

### E1. Model + migration

```python
class RegistroExecucao(TimestampedModel):
    tarefa = models.ForeignKey(Tarefa, null=True, on_delete=models.SET_NULL, ...)
    evento = models.ForeignKey(Evento, null=True, on_delete=models.SET_NULL, ...)
    classe = models.ForeignKey(Classe, null=True, on_delete=models.SET_NULL, ...)
    planejado_min = models.PositiveIntegerField(null=True)
    real_min = models.PositiveIntegerField(null=True)   # opcional (usuário informa)
    remarcado = models.BooleanField(default=False)
    concluido_em = models.DateTimeField(null=True)
```

Escrito pelos fluxos de `services/completion.py`: `concluir(...)` ganha
parâmetro opcional `real_min` (payload `{"real_min": 90}` no endpoint atual —
retrocompatível); `remarcar` grava `remarcado=True`. Nenhuma tela nova é
obrigatória: sem `real_min`, o registro ainda vale para flexibilidade.

### E2. Fatores (`services/adaptacao.py`)

```python
def fator_classe(classe_id):
    """EWMA de real/planejado (α=0.3). Regras: mínimo 3 amostras c/ real_min
    (senão 1.0); clamp 0.5–3.0. Cacheado (locmem/Redis, TTL curto)."""

def flexibilidade_classe(classe_id):
    """Taxa de remarcação da classe (0..1) na janela dos últimos 90 dias."""

def decair_pesos():
    """w ← w + λ·(1.0 − w), λ=0.02, aplicado ao ler (sem cron): gostos antigos
    não viram âncora eterna."""
```

### E3. Integração

- `montar_plano` ganha `usar_fatores=True`: `esforco_efetivo = round(esforco ×
  fator_classe)`; o echo em `preferencias_usadas` expõe os fatores aplicados
  (transparência na UI).
- `construir_contexto` ganha fatos novos: `fatores_classe`,
  `flexibilidade_classe`, `pesos_preferencia` — a IA propõe cenários conhecendo
  o comportamento real ("valoriza fds livre", "trabalho é elástico"). Classes
  flexíveis são as preferidas para mover no replanejar/cenários (via prompt,
  não via código — continua sendo só proposta).

### E4. Testes

- `concluir` com/sem `real_min`; `remarcar` grava registro.
- EWMA: converge, respeita clamp e mínimo de amostras.
- Solver com fator 1.3 aloca 30% a mais para a classe (e o echo expõe).
- Decaimento move pesos rumo a 1.0.
- Contexto contém os fatos novos.

---

## Marco C4 — Servidor MCP + agente (PR 5)

### M1. Servidor MCP (container novo `mcp` no compose)

Processo Python fino (SDK `mcp`, transporte streamable-http) que **só chama a
API HTTP local** (`web:8000`) — zero lógica própria, zero import de Django:

| Tool             | Endpoint                              | Nota                          |
| ---------------- | ------------------------------------- | ----------------------------- |
| `criar_tarefa`   | `POST /tarefas/`                      | título, classe, deadline, esforço |
| `listar_pendentes` | `GET /pendentes`                    |                               |
| `simular_plano`  | `POST /planejamento/calcular`         | what-if — NÃO persiste        |
| `gerar_cenarios` | `POST/GET /planejamento/cenarios`     | encapsula o polling           |
| `escolher_cenario` | `POST /planejamento/cenarios/escolher` |                             |
| `replanejar`     | `POST /planejamento/replanejar[...]`  | simular e aplicar separados   |
| `remarcar`       | endpoint existente                    |                               |

O what-if conversacional ("e se eu estudar Cálculo na quinta?") é
`simular_plano` com entradas hipotéticas + diff — já suportado pela pureza do
solver, sem código novo no backend.

### M2. Agente

- O runtime do agente é **externo e trocável** (Claude, Hermes, etc. — qualquer
  cliente MCP); o investimento durável é a camada de tools acima.
- Realismo de hardware: o 7B/CPU serve para as chamadas únicas com schema;
  agência multi-turno pede modelo maior — documentar no README como apontar um
  cliente MCP para o servidor, e deixar `AGENTE_*` fora do core do backend.
- Integrações Notion/Google Calendar: **views sincronizadas** do plano aplicado
  (fora do escopo deste PR; nunca fonte de verdade).

### M3. Testes

- Tools espelham contratos (testes de contrato contra a API com `respx`/fixtures).
- Compose: serviço `mcp` sobe e responde `initialize`.

---

## Riscos e decisões em aberto

- **Explosão de latência da IA em C1b**: mitigada por UMA chamada para o array
  inteiro + cache; se o 7B degradar com o schema maior, cortar para 3 candidatos.
- **Nível 5 (dias bloqueados) surpreender o usuário**: o plano marca no echo
  quando um dia bloqueado foi usado (alerta "medio", como o teto estourado hoje).
- **`real_min` depende de disciplina do usuário**: por isso é opcional e a
  flexibilidade (que não depende dele) vem junto no C3.
- **Pesos por contexto (prova vs. férias)**: adiado; decisão de v2 condicionada
  a `EscolhaCenario` acumulado mostrar bimodalidade.
