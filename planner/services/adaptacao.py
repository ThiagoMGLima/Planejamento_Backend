"""Fatores adaptativos (nasce no C1b com os pesos; cresce no C3).

Pesos de preferência por ESCOLHA REVELADA (§2.4 da visão): em vez de perguntar
o que o usuário valoriza, o sistema observa qual cenário ele escolhe e move os
pesos das métricas na direção da diferença (EWMA). Guarda-corpos: taxa pequena,
piso/teto por peso (nenhuma métrica morre), e a escolha CRUA fica gravada em
`EscolhaCenario` — dá para trocar a regra e recalcular tudo do zero.
"""

from ..models import PesoPreferencia
from .cenarios import METRICAS

ALFA = 0.1  # taxa de aprendizado (pequena de propósito)
PESO_MIN = 0.2  # piso: nenhuma métrica morre
PESO_MAX = 3.0
PESO_NEUTRO = 1.0


def pesos_atuais():
    """Dict métrica → peso, com neutro (1.0) para o que nunca foi aprendido."""
    salvos = dict(PesoPreferencia.objects.values_list("metrica", "valor"))
    return {m: float(salvos.get(m, PESO_NEUTRO)) for m in METRICAS}


def atualizar_pesos(escolha):
    """EWMA por métrica a partir de uma EscolhaCenario. Retorna os pesos novos.

    Para cada métrica: Δ = métrica normalizada do escolhido − média dos
    rejeitados; w ← clamp(w + ALFA·Δ, PESO_MIN, PESO_MAX). Escolha sem
    rejeitados (lote de 1) não ensina nada. Decaimento lento rumo ao neutro
    entra no C3.
    """
    pesos = pesos_atuais()
    exibidos = escolha.lote or []
    escolhido = next((c for c in exibidos if c["id"] == escolha.escolhido), None)
    rejeitados = [c for c in exibidos if c["id"] != escolha.escolhido]
    if escolhido is None or not rejeitados:
        return pesos

    novos = {}
    for m in METRICAS:
        media_rejeitados = sum(c["metricas_vs_base"][m] for c in rejeitados) / len(
            rejeitados
        )
        delta = escolhido["metricas_vs_base"][m] - media_rejeitados
        valor = min(PESO_MAX, max(PESO_MIN, pesos[m] + ALFA * delta))
        novos[m] = round(valor, 4)
        PesoPreferencia.objects.update_or_create(
            metrica=m, defaults={"valor": novos[m]}
        )
    return novos
