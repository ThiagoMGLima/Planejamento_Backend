"""Estimativa adaptativa da duração dos jobs de IA (EWMA da razão real/prevista).

A fórmula estática (`estimar_tempo_s`: base + por_tarefa·n) foi calibrada para o
planejar-ia; a geração de CENÁRIOS produz 3–6 cenários de saída e leva ~4–5×
mais no CPU (medição 2026-07-03) — a contagem regressiva do front zerava e o
job seguia rodando. Em vez de recalibrar constantes na mão a cada troca de
modelo/hardware, cada task registra a duração real do job e aprendemos, por
família, a RAZÃO real/prevista (EWMA); a estimativa vira `prevista × razão`.
Multiplicativo de propósito: preserva a escala com o nº de tarefas da fórmula.

Contrato: `registrar` e `estimar` da MESMA família devem receber a MESMA
`prevista_s` (a mesma fórmula), senão a razão aprendida não se aplica.
Famílias em uso: "planejar_ia", "cenarios", "refino".
"""

from django.core.cache import cache

# Peso da observação nova. Alto (0.4) de propósito: app single-user tem poucas
# amostras, e uma troca de modelo/hardware precisa convergir em poucos jobs.
ALPHA = 0.4

# Um mês sem uso ⇒ volta à semente (hardware/modelo podem ter mudado).
TTL_S = 60 * 60 * 24 * 30

# Semente da família "cenarios": antes de qualquer histórico, a geração de
# cenários já parte de ~4× a fórmula do planejar-ia (medição no 7B em CPU).
FATOR_CENARIOS = 4


def registrar(familia, duracao_s, prevista_s):
    """EWMA da razão real/prevista da família. Devolve a razão atualizada.

    Chamar só quando a IA de fato rodou (sucesso): degradações instantâneas
    (Ollama fora do ar, IA desligada) não representam a espera dos jobs reais
    e afundariam a razão.
    """
    razao = duracao_s / max(prevista_s, 1)
    chave = f"tempo_razao:{familia}"
    atual = cache.get(chave)
    nova = razao if atual is None else (1 - ALPHA) * atual + ALPHA * razao
    cache.set(chave, nova, timeout=TTL_S)
    return nova


def estimar(familia, prevista_s):
    """Estimativa honesta: `prevista × razão aprendida` (1.0 sem histórico)."""
    razao = cache.get(f"tempo_razao:{familia}")
    if razao is None:  # `or` engoliria uma razão 0.0 legítima
        razao = 1.0
    return max(1, round(prevista_s * razao))
