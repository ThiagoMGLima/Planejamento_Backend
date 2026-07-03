"""Fatores adaptativos (Marcos C1b e C3) — o sistema aprende observando.

Três aprendizados, todos estatística simples (sem ML), sempre por CÓDIGO:
- **pesos de preferência** (C1b): escolha revelada de cenários → EWMA por
  métrica; ordenam e sugerem, nunca filtram;
- **fator de estimativa por classe** (C3): EWMA de real/planejado — "Cálculo
  costuma levar 1.3× o que você estima"; o solver multiplica o esforço;
- **flexibilidade por classe** (C3): taxa de remarcação — classes elásticas
  são o amortecedor preferencial de cenários/replanejamento (via prompt).

Este módulo importa só de models (nenhum outro service) — é a ponta da cadeia,
o que permite a planejamento/planejamento_ia/cenarios importarem daqui sem
ciclo. `METRICAS` mora aqui pelo mesmo motivo.
"""

from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import PesoPreferencia, RegistroExecucao

# Métricas comparáveis entre cenários (§2.3 da visão). Todas por CÓDIGO.
METRICAS = (
    "pico_min_dia",
    "dias_livres",
    "fds_livres",
    "folga_media_h",
    "min_fora_janela",
    "fragmentacao",
    "nao_alocado_min",
)

# Pesos de preferência (C1b)
ALFA = 0.1  # taxa de aprendizado (pequena de propósito)
PESO_MIN = 0.2  # piso: nenhuma métrica morre
PESO_MAX = 3.0
PESO_NEUTRO = 1.0
LAMBDA_DECAIMENTO = 0.02  # decaimento lento rumo ao neutro (gostos mudam)

# Fator de estimativa por classe (C3)
FATOR_ALFA = 0.3
FATOR_MIN_AMOSTRAS = 3  # menos que isso ⇒ neutro (1.0)
FATOR_MINIMO, FATOR_MAXIMO = 0.5, 3.0
FATOR_CACHE_TTL_S = 60  # locmem/Redis; TTL curto
JANELA_FLEXIBILIDADE = timedelta(days=90)


# --------------------------------------------------------------------------- #
# Pesos de preferência (escolha revelada)                                      #
# --------------------------------------------------------------------------- #
def pesos_atuais():
    """Dict métrica → peso, com neutro (1.0) para o que nunca foi aprendido."""
    salvos = dict(PesoPreferencia.objects.values_list("metrica", "valor"))
    return {m: float(salvos.get(m, PESO_NEUTRO)) for m in METRICAS}


def decair_pesos():
    """w ← w + λ·(1.0 − w), aplicado ao ler (sem cron). Retorna os pesos novos.

    Gostos antigos não viram âncora eterna: cada leitura puxa 2% de volta ao
    neutro; o EWMA das escolhas novas reafirma o que continua valendo.
    """
    novos = {}
    for m, w in pesos_atuais().items():
        if w == PESO_NEUTRO:
            novos[m] = w
            continue
        novos[m] = round(w + LAMBDA_DECAIMENTO * (PESO_NEUTRO - w), 4)
        PesoPreferencia.objects.update_or_create(
            metrica=m, defaults={"valor": novos[m]}
        )
    return novos


def atualizar_pesos(escolha):
    """EWMA por métrica a partir de uma EscolhaCenario. Retorna os pesos novos.

    Para cada métrica: Δ = métrica normalizada do escolhido − média dos
    rejeitados; w ← clamp(w + ALFA·Δ, PESO_MIN, PESO_MAX). Escolha sem
    rejeitados (lote de 1) não ensina nada.
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


# --------------------------------------------------------------------------- #
# Fatores por classe (C3) — do RegistroExecucao                                #
# --------------------------------------------------------------------------- #
def fator_classe(classe_id):
    """EWMA de real/planejado da classe (α=0.3). Cacheado (TTL curto).

    Regras: mínimo FATOR_MIN_AMOSTRAS registros com `real_min` (senão 1.0);
    clamp 0.5–3.0. `classe_id` inválido/None ⇒ neutro.
    """
    if classe_id is None:
        return 1.0
    chave = f"fator_classe:{classe_id}"
    hit = cache.get(chave)
    if hit is not None:
        return hit

    try:
        razoes = [
            r / p
            for r, p in RegistroExecucao.objects.filter(
                classe_id=classe_id, real_min__isnull=False, planejado_min__gt=0
            )
            .order_by("criado_em")
            .values_list("real_min", "planejado_min")
        ]
    except (ValueError, TypeError, ValidationError):  # id que nem é UUID
        return 1.0

    if len(razoes) < FATOR_MIN_AMOSTRAS:
        fator = 1.0
    else:
        f = 1.0  # semente neutra; converge com as amostras
        for razao in razoes:
            f = (1 - FATOR_ALFA) * f + FATOR_ALFA * razao
        fator = round(min(FATOR_MAXIMO, max(FATOR_MINIMO, f)), 2)

    cache.set(chave, fator, FATOR_CACHE_TTL_S)
    return fator


def flexibilidade_classe(classe_id):
    """Taxa de remarcação (0..1) da classe nos últimos 90 dias.

    Alta ⇒ classe elástica (candidata preferencial a mover em cenários e
    replanejamento); baixa ⇒ rígida (aula, estágio), fica intocada. Não
    depende de `real_min`. Sem registros ⇒ 0.0.
    """
    if classe_id is None:
        return 0.0
    try:
        registros = RegistroExecucao.objects.filter(
            classe_id=classe_id, criado_em__gte=timezone.now() - JANELA_FLEXIBILIDADE
        ).values_list("remarcado", flat=True)
    except (ValueError, TypeError, ValidationError):
        return 0.0
    registros = list(registros)
    if not registros:
        return 0.0
    return round(sum(registros) / len(registros), 2)
