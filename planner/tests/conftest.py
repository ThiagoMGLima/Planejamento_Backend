"""Fixtures compartilhadas dos testes (Fase 0B, PR1).

`perfil` é o perfil local — o mesmo que a API resolve enquanto não há login.
Testes de API e de service usam ele e continuam falando de "os dados".

`outro_perfil` só aparece onde o assunto é isolamento. Fora dali ele não tem
utilidade: um teste com um perfil só não distingue "global" de "do dono", e é
por isso que a suíte antiga passaria verde com vazamento dentro.
"""

import pytest

from planner.tests.factories import OutroPerfilFactory, PerfilFactory


@pytest.fixture
def perfil(db):
    return PerfilFactory()


@pytest.fixture
def outro_perfil(db):
    return OutroPerfilFactory()
