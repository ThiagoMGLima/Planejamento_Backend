"""Manager que exige escopo por dono (0B.10 — Fase 0B, PR1).

O isolamento entre contas não é uma convenção que alguém precisa lembrar: é o
**default**. Toda consulta a um model por-dono nasce recusada, e quem consulta
diz de quem são os dados:

    Evento.objects.do_dono(perfil).filter(...)   # o caminho normal
    Evento.objects.sem_escopo().filter(...)      # varredura global, explícita

`Evento.objects.all()` levanta `EscopoAusente`. Isso é deliberado (princípio 9
do ROADMAP): antes, o isolamento dependia de acertar 32 pontos de query seguidos,
para sempre, inclusive em código que ainda não existe — o nível mais fraco da
escala ("convenção documentada"). Com a recusa no default, esquecer o escopo é
um **erro em teste**, não um vazamento silencioso.

`sem_escopo()` é o escape hatch de propósito: é uma string grepável que aparece
na revisão. Só os seeds e o admin têm motivo para usá-la.

A guarda mora no QuerySet, não no Manager, porque é na avaliação que a consulta
vira dados — `Evento.objects.filter(x=1)` sozinho não vazou nada; quem vaza é o
`_fetch_all`. Os métodos que produzem resultado sem passar por ele (`count`,
`exists`, `update`, `delete`, …) são interceptados um a um logo abaixo.
"""

from django.db import models


class EscopoAusente(RuntimeError):
    """Consulta a um model por-dono sem dizer de quem são os dados."""


MENSAGEM = (
    "{model}.objects foi consultado sem escopo de dono. Use "
    "`.do_dono(perfil)` para os dados de um perfil, ou `.sem_escopo()` "
    "(explícito e grepável — só seeds e admin) para varrer todos os perfis."
)


class EscopoQuerySet(models.QuerySet):
    """QuerySet que se recusa a ser avaliado antes de saber de quem são os dados."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._escopado = False

    def _clone(self):
        # Sem isto, o primeiro `.filter()` depois do `do_dono()` perderia a marca
        # e a consulta legítima passaria a levantar.
        clone = super()._clone()
        clone._escopado = self._escopado
        return clone

    def do_dono(self, dono):
        """Escopa a consulta a um perfil. Aceita instância de `Perfil` ou o id."""
        if dono is None:
            raise EscopoAusente(
                f"{self.model.__name__}.do_dono(None): o dono é obrigatório. "
                "Se a intenção é varrer todos os perfis, use `.sem_escopo()`."
            )
        clone = self.filter(dono=dono)
        clone._escopado = True
        return clone

    def sem_escopo(self):
        """Varredura global deliberada (seeds, admin). Grepável de propósito."""
        clone = self._clone()
        clone._escopado = True
        return clone

    def _exigir_escopo(self):
        if not self._escopado:
            raise EscopoAusente(MENSAGEM.format(model=self.model.__name__))

    # --- pontos de avaliação ------------------------------------------------ #
    # `_fetch_all` cobre iteração, len(), bool(), get(), first(), values_list()
    # e afins. Os demais produzem resultado sem passar por ele.
    def _fetch_all(self):
        self._exigir_escopo()
        super()._fetch_all()

    def count(self):
        self._exigir_escopo()
        return super().count()

    def exists(self):
        self._exigir_escopo()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._exigir_escopo()
        return super().aggregate(*args, **kwargs)

    def iterator(self, *args, **kwargs):
        self._exigir_escopo()
        return super().iterator(*args, **kwargs)

    def in_bulk(self, *args, **kwargs):
        self._exigir_escopo()
        return super().in_bulk(*args, **kwargs)

    def update(self, **kwargs):
        self._exigir_escopo()
        return super().update(**kwargs)

    def delete(self):
        # O ponto mais perigoso do PR: sem escopo,
        # `Evento.objects.filter(id__in=ids).delete()` apaga evento alheio a
        # partir de um id forjado (ver services/replanejamento.py).
        self._exigir_escopo()
        return super().delete()


class EscopoManager(models.Manager.from_queryset(EscopoQuerySet)):
    """Manager default dos models por-dono. `create()` segue livre — o `dono`
    é NOT NULL no banco, então criar sem ele falha na hora, sem precisar de guarda."""

    def raw(self, *args, **kwargs):
        """SQL cru não passa pelo QuerySet, logo não passa pela guarda.

        Bloqueado em vez de documentado: por baixo, `raw()` devolve um
        `RawQuerySet` que não herda nada daqui, então deixá-lo aberto seria uma
        porta dos fundos silenciosa — o nível "convenção documentada" que o
        princípio 9 do ROADMAP recusa. Quem realmente precisar de SQL cru usa
        `Model._base_manager.raw(...)`, que é explícito e grepável.
        """
        raise EscopoAusente(
            f"{self.model.__name__}.objects.raw() não é escopado por dono. "
            "Use o ORM com `.do_dono(perfil)`, ou "
            f"`{self.model.__name__}._base_manager.raw(...)` se o SQL cru for "
            "mesmo necessário (e então escope na mão)."
        )
