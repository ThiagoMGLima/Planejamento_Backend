"""Knob interno → frase que o usuário lê (Fase 1.1, PR C).

**Por que isto é código e não prompt.** O `SYSTEM_PROMPT` do planejador já
mandava, em maiúsculas, nunca citar nome de campo nem UUID — e o agente, que
não tinha a regra, respondeu ao usuário com `classe_id: c9a351f9-…`. Regra em
prompt é pedido; tabela é garantia. É a mesma disciplina do grounding
determinístico em `agente.py` (classes e datas entram como FATOS porque copiar
é confiável e parafrasear não é), aplicada ao vocabulário.

Quem escreve a frase é este módulo. O modelo escolhe **qual** knob propor; o
texto que chega ao usuário nunca passa por ele.

Nenhuma frase daqui pode conter nome de campo, id ou jargão — há teste que
falha se contiver (`test_vocabulario.py`).
"""

from datetime import date

DIAS_ADJETIVO = [
    "segundas",
    "terças",
    "quartas",
    "quintas",
    "sextas",
    "sábados",
    "domingos",
]

# Faixas reconhecidas por nome. Fora delas, a frase cai no genérico
# "só entre HH:MM e HH:MM" — que também é linguagem natural.
_FAIXAS = (
    (5 * 60, 12 * 60, "só de manhã"),
    (12 * 60, 18 * 60, "só à tarde"),
    (18 * 60, 24 * 60, "só à noite"),
)


def _hhmm(minutos):
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def _data(valor):
    d = date.fromisoformat(valor) if isinstance(valor, str) else valor
    return f"{d.day:02d}/{d.month:02d}"


def _lista(itens):
    """['a', 'b', 'c'] → 'a, b e c' (sem vírgula antes do 'e')."""
    itens = list(itens)
    if len(itens) == 1:
        return itens[0]
    return f"{', '.join(itens[:-1])} e {itens[-1]}"


def _janela(ini_min, fim_min):
    for faixa_ini, faixa_fim, nome in _FAIXAS:
        if faixa_ini <= ini_min and fim_min <= faixa_fim:
            return nome
    return f"só entre {_hhmm(ini_min)} e {_hhmm(fim_min)}"


def frase(knob, valor):
    """Frase para um knob, ou None quando o valor não diz nada ao usuário.

    `valor` vem já validado (`validar_diretrizes`). Valor irreconhecível devolve
    None em vez de levantar: esta camada é cosmética e nunca pode derrubar um
    plano — mesma regra do guarda-corpo.
    """
    try:
        if knob == "estrategia":
            if valor == "TARDE":
                return "estuda perto do prazo"
            if valor == "CEDO":
                return "começa o quanto antes"
            return None
        if knob == "nao_antes_de":
            return f"não começa antes de {_data(valor)}"
        if knob == "nao_depois_de":
            return f"termina até {_data(valor)}"
        if knob == "janela":
            ini, fim = valor
            return _janela(ini, fim)
        if knob == "dias_permitidos":
            dias = sorted(set(valor))
            if not dias or len(dias) == 7:
                return None
            return "só às " + _lista(DIAS_ADJETIVO[d] for d in dias)
        if knob == "buffer_dias":
            n = int(valor)
            if n <= 0:
                return None
            return "termina na véspera" if n == 1 else f"termina {n} dias antes"
        if knob == "max_min_por_dia":
            return f"no máximo {int(valor)} min por dia"
        if knob == "prioridade":
            n = int(valor)
            if n >= 5:
                return "prioridade máxima"
            if n == 4:
                return "mais importante que o resto"
            if n == 2:
                return "menos importante que o resto"
            if n <= 1:
                return "prioridade mínima"
            return None  # 3 é o neutro: não vale dizer nada
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return None


def descrever(ajuste):
    """Lista de frases para o dict de ajustes de UMA tarefa.

    `janela_inicio`/`janela_fim` viram uma frase só (a janela é uma condição,
    não duas). Ordem estável para o texto não dançar entre execuções.
    """
    if not isinstance(ajuste, dict):
        return []
    frases = []
    for knob in ("estrategia", "nao_antes_de", "nao_depois_de"):
        if knob in ajuste:
            f = frase(knob, ajuste[knob])
            if f:
                frases.append(f)
    ini, fim = ajuste.get("janela_inicio"), ajuste.get("janela_fim")
    if ini is not None and fim is not None:
        from .planejamento_ia import _hhmm_como_min

        ini_min, fim_min = _hhmm_como_min(ini), _hhmm_como_min(fim)
        if ini_min is not None and fim_min is not None:
            f = frase("janela", (ini_min, fim_min))
            if f:
                frases.append(f)
    for knob in ("dias_permitidos", "buffer_dias", "max_min_por_dia"):
        if knob in ajuste:
            f = frase(knob, ajuste[knob])
            if f:
                frases.append(f)
    return frases


def pergunta(titulo, knob, valor):
    """Texto da pergunta que o plano devolve. None se o knob não render frase.

    O modelo escolhe a tarefa, o knob e o valor; a redação é daqui.
    """
    f = frase(knob, valor)
    if not f:
        return None
    return f"Quer que «{titulo}» {f}?"
