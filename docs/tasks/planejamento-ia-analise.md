# Plano — Planejamento assistido por IA (solver + IA melhora + valida + entrega)

> **Status: rascunho para discussão.** O solver determinístico continua sendo o
> motor que **monta e garante a validade** do plano. A IA (Ollama, local)
> **melhora** o plano do solver e **explica** o resultado. A entrega é **um único
> plano polido** + trade-offs + sugestões.
>
> Princípio: **a IA melhora via diretrizes de alto nível; o solver realiza →
> válido por construção.** A IA nunca posiciona horários na v1.

## 1. Objetivo e escopo

Fluxo escolhido: **solver monta → IA melhora → (válido por construção) → entrega
o plano melhorado + trade-offs + sugestões.**

- **Fase A (esta task):** a IA melhora emitindo **diretrizes** (prioridades e
  ajustes por tarefa) que o **solver re-roda**. Como o solver nunca gera conflito,
  o plano melhorado é **válido por construção** — não há "rejeição".
- **Fase B (futuro):** a IA edita o plano **concreto** (reposiciona sessões), com
  validador + fallback. Reusa o validador e o contexto desta fase.

Decisão de entrega: **só o plano já melhorado** (o usuário espera a IA, com
loading). Perde o "instantâneo" e o determinismo estrito — aceito conscientemente;
mitigado com `temperature 0` + cache.

## 2. Fluxo (pipeline assíncrono, UMA chamada de IA)

```
POST /planejar-ia  ──► valida (422/400) ──► enfileira Celery ──► 202 {job_id}
                                                  │
   1. solver monta o PLANO BASE (montar_plano, determinístico)
                                                  │
   2. construir CONTEXTO grounded (fatos exatos por tarefa/dia)
                                                  │
   3. IA (1 chamada) ──► DIRETRIZES + resumo + trade-offs + sugestões  [qwen2.5:7b]
                                                  │
   4. valida diretrizes (serializer) ──► solver RE-RODA ──► PLANO MELHORADO (válido)
                                                  │
   5. CÓDIGO calcula consequências concretas (não-alocado, dias carregados) → alertas
                                                  │
GET /planejar-ia/{job_id} ◄── {plano, resumo, trade_offs, alertas, sugestoes, prefs_usadas}
```

- **Uma chamada de IA** (não duas): a IA explica a **estratégia** que está aplicando
  (verdade independe dos horários exatos). Os **números concretos** (o que não coube,
  carga por dia) vêm do **código** após o solver re-rodar — grounding mantido, ~½ da latência.
- **Assíncrono** (Celery + Redis): o plano inteiro espera o Ollama; front faz polling.
- `/calcular` (solver puro) continua como building block (passo 1), **fallback** e
  possível "modo rápido sem IA".
- **Fallback:** IA falha → entrega o **plano base** (sem melhoria) + aviso.

## 3. Componentes a criar

### 3.1 Extensão do solver (`services/planejamento.py`) — habilita as diretrizes
Hoje o solver aceita só prefs globais + EDF. Para a IA "melhorar via diretrizes",
o solver passa a aceitar **3 knobs por tarefa** (todos opcionais, suaves):
- `prioridade` (1..5) → desempate do EDF (deadline continua mandando). É o principal
  lever: o que a IA enxerga e o EDF não (Prova > Organizar bibliografia).
- `buffer_dias` → deadline efetiva = `deadline - buffer` (terminar N dias antes).
- `max_min_por_dia` por tarefa → override do teto global; é o lever de "espaçar"
  (baixa o teto pra uma tarefa não ser grindada). O solver já conta min por (tarefa,dia).

Cortados da v1 (entram depois se faltarem): `espacar` separado (redundante com
`max_min_por_dia`) e `permitir_fds` por tarefa (a cascata de relaxamento já cobre).
Incremental ao que já existe. **Plano melhorado = saída do solver estendido → válido por construção.**

> **Refactor:** extrair `montar_plano(tarefa_ids, a_partir_de, preferencias,
> diretrizes=None)` para `services/planejamento.py`, usado pelo `/calcular`
> (sem diretrizes) e pelo pipeline (com diretrizes).

### 3.2 Serviço Ollama (docker-compose)
**CPU primeiro** (imagem padrão, prova o pipeline sem driver); **GPU/ROCm depois**
(passo 7, otimização). Versão CPU:
```yaml
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_models:/root/.ollama
# volumes: ollama_models: {}
```
Para GPU AMD depois: trocar para `image: ollama/ollama:rocm` + `devices: [/dev/kfd,
/dev/dri]` + `group_add: [video]` + (se preciso) `HSA_OVERRIDE_GFX_VERSION=11.0.0`
(a RX 7600 é gfx1102).

- **Modelo: `qwen2.5:7b-instruct` (Q4_K_M, ~4,7 GB)** primário; **`qwen2.5:3b`**
  fallback. (RX 7600, 8 GB VRAM → 7B Q4 cabe inteiro na GPU quando ela entrar.)
- Como é assíncrono, latência alta (CPU) não trava o app.
- **❓ validar:** latência real da chamada no seu hardware (CPU agora, GPU depois).

### 3.3 Configuração (`settings.py` + `.env`)
```
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_TIMEOUT=60
IA_PLANEJAMENTO_ENABLED=1     # flag: 0 desliga a IA (entrega plano base puro)
```

### 3.4 `services/planejamento_ia.py`
1. **`construir_contexto(plano_base, nao_alocado, tarefas, ocupado, prefs, agora)`**
   → fatos **já calculados** (grounding): por tarefa (esforço, restante, sessões,
   dias usados), por dia (carga), capacidade livre antes de cada deadline, prefs.
2. **`gerar_melhoria(contexto)`** → **1 chamada** de IA, schema JSON →
   `{diretrizes, resumo, trade_offs, sugestoes}` (estratégia + explicação juntas).
3. **`validar_diretrizes(diretrizes, tarefas_validas)`** → guarda-corpo: `tarefa_id`
   existe; ranges válidos (reusa lógica do `PreferenciasSerializer`); descarta o
   inválido. (Mesmo inválida, o solver ignora com segurança.)
4. **`alertas_do_plano(plano_melhorado, nao_alocado)`** → **código** gera os alertas
   concretos (o que não coube, risco de prazo) — grounded, sem IA.

### 3.5 Celery task + cache
`planejar_ia_task(tarefa_ids, a_partir_de, preferencias)` roda o pipeline (passos
1–5). **Cache no Redis** por `(tarefa_ids + prefs + hash do plano base)` — entrada
idêntica retorna sem chamar o Ollama de novo.

### 3.6 Endpoints + serializers
- `POST /api/v1/planejamento/planejar-ia` — reusa `CalcularSerializer`, valida
  422/400 **síncrono**, checa cache, enfileira, retorna `202 {job_id, status}`.
- `GET /api/v1/planejamento/planejar-ia/{job_id}` — `AsyncResult`: `processando`
  / `pronto` (+ resultado) / `erro`.
- `/calcular` e `/aplicar` **inalterados** (o `aplicar` recebe as sessões finais,
  já melhoradas, igual hoje).

## 4. Contrato de saída da IA (JSON schema — UMA chamada)

```json
{
  "diretrizes": {
    "prioridades": { "<tarefa_id>": 3 },
    "ajustes_por_tarefa": {
      "<tarefa_id>": { "buffer_dias": 1, "max_min_por_dia": 60 }
    }
  },
  "resumo": "string curta, objetiva, PT-BR",
  "trade_offs": [
    "Priorizei Química sobre Estatística porque a prova é antes." ],
  "sugestoes": [
    { "tipo": "ajustar_pref", "descricao": "...",
      "acao": { "preferencias": { "<chave>": <valor> } } } ]
}
```
- A IA explica a **estratégia** (`resumo`, `trade_offs`) que ela mesma está aplicando
  — verdade independente dos horários realizados.
- `alertas` (o que não coube, risco de prazo) **NÃO vêm da IA** — o **código** calcula
  do plano realizado (grounded) e anexa na resposta final.
- `sugestoes` = patch de preferências (aplicar = re-rodar o pipeline).
- Tom **objetivo/factual** (a saída é consumida programaticamente).

## 5. Prompt / grounding (esboço)

- **System (PT-BR, tom objetivo):** "Você melhora e explica planos de estudo.
  Receberá FATOS já calculados e o plano atual. **NUNCA invente números/horários/datas.**
  Numa única resposta: escolha prioridades e ajustes por tarefa (do vocabulário dado)
  e explique de forma **objetiva e factual** a estratégia, os trade-offs e sugestões de
  preferência. Sem linguagem floreada. Responda no schema."
- `temperature: 0` + `format: <schema>` (força JSON válido).
- **Grounding total no código** (decidido): a IA frasa/prioriza/escolhe knobs,
  nunca calcula.

## 6. Tratamento de erro / degradação
- Ollama down/timeout ou `IA_PLANEJAMENTO_ENABLED=0` → entrega **plano base** do
  solver (passo 1) + `ia_indisponivel: true`. O app nunca fica sem plano.
- Diretriz/saída malformada → `validar_*` descarta o inválido; pior caso = plano
  base sem melhoria.

## 7. Testes (sem bater no modelo real)
- **Mock do cliente Ollama** (JSON fixo) → testes determinísticos.
- Solver estendido: `prioridade`/`max_min_por_dia` por tarefa/`buffer_dias`/`espacar`
  produzem o efeito esperado e **continuam sem sobreposição / soma correta**.
- `construir_contexto`: fatos corretos. `validar_diretrizes`: descarta inválidos.
- Pipeline com Celery **eager** + Ollama mockado: caminho feliz e fallback (IA off).

## 8. Pontos — consolidado

**Decididos:**
1. ✅ **Como a IA melhora** — Fase A: **diretrizes** (solver realiza, válido por
   construção). Fase B (depois): edição concreta + validador + fallback.
2. ✅ **Entrega** — só o plano já melhorado (espera a IA, com loading).
3. ✅ **Modelo** — `qwen2.5:7b` primário / `qwen2.5:3b` fallback (RX 7600, 8 GB).
4. ✅ **Job** — Celery + Redis `AsyncResult` (sem migração).
5. ✅ **Sugestões** — só patch de preferências na v1.
6. ✅ **Grounding** — total no código; a IA não calcula.
7. ✅ **Cache** — Redis por `(tarefa_ids + prefs + hash do plano base)`.
8. ✅ **Segurança** — saída validada por schema + serializer; risco baixo. Aceito.

9. ✅ **Vocabulário das diretrizes** — 3 knobs na v1: `prioridade`, `buffer_dias`,
   `max_min_por_dia` (por tarefa). Amplia depois se faltar.
10. ✅ **Uma chamada de IA** — diretrizes + resumo + trade-offs juntos; o código
    calcula os alertas concretos. Grounding mantido, ~½ da latência.
11. ✅ **Tom** — objetivo/factual (saída consumida programaticamente).
12. ✅ **Hardware** — implementar no **CPU primeiro**, ligar **GPU/ROCm depois**
    como otimização. Fallback CPU/3B sempre garantido.

**Em aberto (validar na implementação):**
13. **Latência real** da chamada no seu hardware (CPU agora; GPU depois).

## 9. Sequência de implementação (quando aprovarmos)
1. Estender o solver (3 knobs por tarefa) + extrair `montar_plano(..., diretrizes)`.
2. `ollama` no compose (**CPU primeiro**) + baixar `qwen2.5:7b` + **medir latência**.
3. `services/planejamento_ia.py` (contexto → 1 chamada → validação → alertas) + mock nos testes.
4. Celery task + cache + 2 endpoints + serializer de status.
5. Testes (solver estendido, contexto, validação, pipeline eager, fallback).
6. **Atualizar** a task do front para o novo contrato (plano vem do `planejar-ia` assíncrono).
7. (Otimização) ligar GPU/ROCm e re-medir latência.
```
