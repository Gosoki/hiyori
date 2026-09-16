"""Shared fixtures.

Tests run OFFLINE by default against captured upstream payloads in fixtures/, so
they are deterministic and fast. The handful of tests that check our assumptions
still hold against the real services are marked `live` and deselected by default
(see pytest.ini); run them with `pytest -m live`.
"""
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BACKEND))


def load_json(name):
    return json.loads((FIXTURES / name).read_text())


def load_bytes(name):
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def tokyo():
    """The default city config, as config.CITIES defines it."""
    import config
    return config.CITIES[0]


@pytest.fixture
def jma_tokyo():
    return load_json("jma_forecast_tokyo.json")


@pytest.fixture
def p2p_quakes():
    return load_json("p2p_551.json")


@pytest.fixture
def p2p_eews():
    return load_json("p2p_556.json")


@pytest.fixture(autouse=True)
def _clean_module_state():
    """Reset the module-level caches between tests.

    weather memoizes today's temperatures and main.py keeps feed caches; leaking
    either between tests would make results depend on execution order.
    """
    yield
    try:
        import weather
        weather._today_temp_memo.clear()
        weather._met_cache.clear()
    except ImportError:
        pass
