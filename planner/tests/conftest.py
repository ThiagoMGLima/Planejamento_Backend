"""Fixtures compartilhadas dos testes (Fase 0B, PR1).

`perfil` é o perfil local — o mesmo que a API resolve enquanto não há login.
Testes de API e de service usam ele e continuam falando de "os dados".

`outro_perfil` só aparece onde o assunto é isolamento. Fora dali ele não tem
utilidade: um teste com um perfil só não distingue "global" de "do dono", e é
por isso que a suíte antiga passaria verde com vazamento dentro.
"""

import pytest

from planner.tests.factories import OutroPerfilFactory, PerfilFactory


@pytest.fixture(autouse=True)
def _telemetria_desligada(settings):
    """A suíte não escreve no JSONL de telemetria real (0A.3).

    Sem isto, qualquer teste que exercite o caminho do `llm.gerar_json` grava no
    arquivo do desenvolvedor: rodar a suíte uma vez injetou 11 registros
    sintéticos (`gpt-caseiro`, providers sem chave, timeouts forjados) no meio
    dos dados reais. Como esse arquivo é a base da decisão da Fase 2, ruído ali
    não é sujeira cosmética — é dado falso numa decisão de produto.

    Quem testa a telemetria religa via a fixture `jsonl`, apontando para tmp_path.
    """
    settings.TELEMETRIA_ENABLED = False


@pytest.fixture
def perfil(db):
    return PerfilFactory()


@pytest.fixture
def outro_perfil(db):
    return OutroPerfilFactory()
