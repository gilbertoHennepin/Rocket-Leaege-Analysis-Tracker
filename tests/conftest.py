from pathlib import Path

import pytest

from rltracker import parse_replay

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def duel():
    return parse_replay(FIXTURES / "duel.replay")


@pytest.fixture(scope="session")
def doubles():
    return parse_replay(FIXTURES / "doubles.replay")


@pytest.fixture(scope="session")
def standard():
    return parse_replay(FIXTURES / "standard.replay")


@pytest.fixture(scope="session")
def all_matches(duel, doubles, standard):
    return [duel, doubles, standard]
