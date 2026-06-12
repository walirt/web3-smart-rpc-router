"""Shared pytest fixtures for the Web3 Smart RPC Router test suite.

These fixtures are intentionally tiny: they hand tests plain dicts (or
``pathlib.Path`` objects) that the model layer can be exercised against,
rather than pre-built Pydantic instances. That keeps each test free to
add, mutate, or remove keys to drive the validation paths it cares
about, without fighting a frozen factory output.

The fixtures mirror the field set declared in :mod:`core.models` — keep
them in sync if new required fields land in the schema.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def valid_global_dict() -> dict[str, Any]:
    """A minimal valid ``global`` block.

    All four fields required by :class:`core.models.GlobalSettings` are
    populated with in-range values; ``max_retries`` matches the
    documented default so a test that builds a full config from this
    fixture plus a single node is guaranteed to round-trip.
    """
    return {
        "listen_port": 8545,
        "probe_interval_seconds": 5.0,
        "request_timeout_seconds": 10.0,
        "routing_strategy": "round_robin",
        "max_retries": 3,
    }


@pytest.fixture
def valid_node_dict() -> dict[str, Any]:
    """A minimal valid single ``rpc_nodes`` entry.

    All optional fields (``weight``, ``headers``) are pinned to their
    documented defaults so tests can compare against an explicit
    "nothing was added" baseline.
    """
    return {
        "provider": "test-provider",
        "url": "https://example.com",
        "priority": 1,
        "weight": 1,
        "headers": {},
    }


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> Callable[[dict[str, Any], str], Path]:
    """Factory fixture: write ``data`` as YAML and return the path.

    Usage::

        def test_load_config(tmp_config_file):
            path = tmp_config_file({"global": {...}, "rpc_nodes": [...]})
            cfg = load_config(path)

    The file is created inside pytest's per-test ``tmp_path`` and is
    automatically cleaned up when the test ends. ``name`` defaults to
    ``"config.yaml"`` but can be overridden for tests that need to
    exercise non-default filenames (e.g. the ``FileNotFoundError`` path
    in :mod:`tests.test_config`).
    """
    def _make(data: dict[str, Any], name: str = "config.yaml") -> Path:
        path = tmp_path / name
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    return _make
