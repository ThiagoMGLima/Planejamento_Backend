"""Registro das chamadas de IA (Fase 0A.3) — o dado que decide a Fase 2.

Irmão do `tempos.py`, e **deliberadamente separado dele** (decisão Q3 do gate):

| | `tempos.py` | este módulo |
| --- | --- | --- |
| mede | o **job** inteiro (solver + DB + IA) | a **chamada** de IA |
| guarda | 1 escalar EWMA no cache, TTL 30d | 1 linha por chamada, em disco |
| serve | contagem regressiva do front | decidir IA local vs API (Fase 2) |

**Não unifique os dois.** `tempos` é estimador volátil a serviço da UI; isto é
histórico durável a serviço de uma decisão de produto. Acoplá-los faria uma
mudança de observabilidade quebrar a contagem regressiva do usuário.

**Nunca registra conteúdo** (decisão Q8): nada de prompt, contexto, título de
tarefa, diretriz ou resposta do modelo — só contagens, durações e
identificadores. O `construir_contexto` carrega a agenda inteira do usuário, e o
beta roda na máquina de terceiros. Nem atrás de flag de debug: flag é ligada
para investigar e nunca desligada, e pelo princípio 9 isso é "convenção
documentada", o degrau mais fraco da tabela.

**Nunca derruba a chamada de IA.** Qualquer falha ao registrar vira `warning` e
o pipeline segue. Mesmo espírito de `validar_diretrizes`, que nunca levanta
(princípio 6: a IA não é caminho crítico — e observar a IA, menos ainda).
"""

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# As quatro famílias de chamada. "planejar_ia", "cenarios" e "refino" coincidem
# de propósito com as famílias do `tempos.py` — a mesma origem, medida noutra
# granularidade. "agente" só existe aqui: o `tempos` não o cobre (decisão Q4b).
FAMILIAS = ("planejar_ia", "cenarios", "refino", "agente")

_NS = 1_000_000_000  # o Ollama reporta durações em nanossegundos


class _Medicao:
    """Coletor entregue pelo `medir`. O caller preenche o que só se conhece
    depois da resposta (tokens, durações internas do provider)."""

    __slots__ = ("tokens_entrada", "tokens_saida", "carga_s", "geracao_s")

    def __init__(self):
        self.tokens_entrada = None
        self.tokens_saida = None
        self.carga_s = None
        self.geracao_s = None

    def metadados(
        self, *, tokens_entrada=None, tokens_saida=None, carga_s=None, geracao_s=None
    ):
        self.tokens_entrada = tokens_entrada
        self.tokens_saida = tokens_saida
        self.carga_s = carga_s
        self.geracao_s = geracao_s


def de_ollama(resp):
    """Metadados de uma resposta do Ollama → kwargs de `_Medicao.metadados`.

    Campos conferidos empiricamente em 08/08/2026 (ollama-python 0.6.2):
    `prompt_eval_count`, `eval_count` e as durações em ns.

    `load_duration` sai **separado** (decisão Q6) porque é carga de modelo, não
    inferência: numa amostra real ele foi 4,24s de 5,46s. Somado ao resto, a
    matriz da 0A.5 mediria cold start em vez de modelo — e com
    `OLLAMA_KEEP_ALIVE=-1` o normal é ~0, então o caso raro precisa ficar
    visível em vez de diluído.

    O ROADMAP dizia "(modo api) tokens"; medir também no local foi decisão Q2 —
    sem token local não existe tok/s, e sem tok/s a 0A.5 (3b×7b) e a 0A.6
    (CPU×RX 7600) não têm o que comparar.
    """
    d = dict(resp) if not isinstance(resp, dict) else resp

    def seg(chave):
        v = d.get(chave)
        return round(v / _NS, 4) if isinstance(v, (int, float)) else None

    return {
        "tokens_entrada": d.get("prompt_eval_count"),
        "tokens_saida": d.get("eval_count"),
        "carga_s": seg("load_duration"),
        "geracao_s": seg("eval_duration"),
    }


def de_anthropic(resp):
    """Idem, para a resposta da API da Claude. Não há `load_duration` remoto."""
    uso = getattr(resp, "usage", None)
    return {
        "tokens_entrada": getattr(uso, "input_tokens", None),
        "tokens_saida": getattr(uso, "output_tokens", None),
    }


@contextmanager
def medir(familia, provider, modelo, dono_id=None):
    """Mede uma chamada de IA e registra o resultado — inclusive se ela falhar.

        with telemetria.medir("planejar_ia", "ollama", modelo) as m:
            resp = cli.chat(...)
            m.metadados(**telemetria.de_ollama(resp))

    A exceção sempre propaga: o caller precisa seguir degradando para
    `ia_indisponivel: true`. Só passamos a registrá-la no caminho (decisão Q5) —
    taxa de timeout e de indisponibilidade decide local-vs-API tanto quanto
    latência, e hoje `LLMIndisponivel` some sem deixar rastro.
    """
    medicao = _Medicao()
    inicio = time.monotonic()
    ok, erro = True, None
    try:
        yield medicao
    except BaseException as e:
        ok = False
        # A CLASSE, nunca a mensagem: mensagens de erro às vezes ecoam pedaços
        # do payload, e aí o Q8 vazaria pela porta dos fundos.
        erro = type(e).__name__
        raise
    finally:
        try:
            _registrar(
                familia=familia,
                provider=provider,
                modelo=modelo,
                duracao_s=round(time.monotonic() - inicio, 4),
                medicao=medicao,
                ok=ok,
                erro=erro,
                dono_id=dono_id,
            )
        except Exception:  # noqa: BLE001 — telemetria nunca derruba a IA
            logger.warning("falha ao registrar telemetria de IA", exc_info=True)


def _registrar(*, familia, provider, modelo, duracao_s, medicao, ok, erro, dono_id):
    if not settings.TELEMETRIA_ENABLED:
        return

    registro = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "familia": familia,
        "provider": provider,
        "modelo": modelo,
        "duracao_s": duracao_s,
        "tokens_entrada": medicao.tokens_entrada,
        "tokens_saida": medicao.tokens_saida,
        "carga_s": medicao.carga_s,
        "geracao_s": medicao.geracao_s,
        "ok": ok,
        "erro": erro,
        "dono": str(dono_id) if dono_id else None,
    }
    # tok/s derivado aqui, e só quando honesto: precisa da duração de geração
    # isolada. Com o tempo de parede daria um número menor e enganoso, que
    # embute carga de modelo e latência de rede.
    if medicao.tokens_saida and medicao.geracao_s:
        registro["tok_s"] = round(medicao.tokens_saida / medicao.geracao_s, 2)

    logger.info(
        "IA %s/%s %s em %.2fs%s%s",
        familia,
        provider,
        "ok" if ok else f"FALHOU ({erro})",
        duracao_s,
        (
            f" · {medicao.tokens_entrada}→{medicao.tokens_saida} tok"
            if medicao.tokens_saida is not None
            else ""
        ),
        f" · {registro['tok_s']} tok/s" if "tok_s" in registro else "",
    )
    _escrever_jsonl(registro)


def _escrever_jsonl(registro):
    caminho = Path(settings.TELEMETRIA_JSONL)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    linha = json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n"
    # Uma linha, um `write`, modo append: no Linux uma escrita menor que
    # PIPE_BUF (4096 B) com O_APPEND é atômica, então workers concorrentes não
    # entrelaçam linhas. Os registros ficam bem abaixo disso — é o motivo de o
    # conteúdo (Q8) não caber aqui nem se quiséssemos.
    with caminho.open("a", encoding="utf-8") as f:
        f.write(linha)
