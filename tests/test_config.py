"""Tests for the YAML loader in :mod:`core.config`.

Each test exercises one observable behaviour of :func:`load_config` or
:func:`parse_config_dict`. The fixtures from :mod:`tests.conftest`
provide the starting point for the schema-driven cases. The
``__main__`` block is exercised in-process via :func:`runpy.run_module`
with ``run_name="__main__"`` so coverage.py tracks it; the same code
path that ``python -m core.config`` would take.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from core.config import _format_summary, load_config, parse_config_dict
from core.models import RouterConfig


# Suppress the harmless RuntimeWarning that runpy emits when it finds
# `core.config` already in sys.modules (because we imported it at the
# top of this file) before re-executing it with run_name="__main__".
# The module's top-level code is idempotent, so the warning is a
# false positive for our use case.
pytestmark = pytest.mark.filterwarnings(
    "ignore:.*found in sys\\.modules.*:RuntimeWarning"
)


# Path to the sample config.yaml at the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CONFIG = REPO_ROOT / "config.yaml"


def test_load_config_sample_returns_two_nodes():
    """load_config('config.yaml') returns a RouterConfig with 2 nodes."""
    cfg = load_config(SAMPLE_CONFIG)
    assert isinstance(cfg, RouterConfig)
    assert len(cfg.rpc_nodes) == 2
    # Spot-check that the sample's two providers are distinct.
    providers = {node.provider for node in cfg.rpc_nodes}
    assert len(providers) == 2


def test_load_config_nonexistent_path_raises(tmp_path):
    """load_config on a missing path propagates FileNotFoundError."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_load_config_invalid_schema_raises(tmp_config_file, valid_global_dict):
    """A YAML file with a missing required field raises ValidationError."""
    # Missing 'rpc_nodes' — the schema requires it.
    path = tmp_config_file({"global": valid_global_dict})
    with pytest.raises(ValidationError):
        load_config(path)


def test_load_config_broken_yaml_raises(tmp_path):
    """A syntactically broken YAML file raises yaml.YAMLError (not silently swallowed)."""
    path = tmp_path / "broken.yaml"
    # An unclosed flow sequence is a hard parse error in any YAML loader.
    path.write_text("global: [unclosed bracket\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_config(path)


def test_parse_config_dict_is_pure(valid_global_dict, valid_node_dict):
    """parse_config_dict returns structurally identical output for the same input."""
    raw = {"global": valid_global_dict, "rpc_nodes": [valid_node_dict]}

    cfg1 = parse_config_dict(raw)
    cfg2 = parse_config_dict(raw)

    # Pydantic models support __eq__ for structural comparison.
    assert cfg1 == cfg2
    assert cfg1.global_.listen_port == cfg2.global_.listen_port
    assert len(cfg1.rpc_nodes) == len(cfg2.rpc_nodes)
    assert cfg1.rpc_nodes[0].provider == cfg2.rpc_nodes[0].provider


def test_load_config_non_dict_yaml_raises(tmp_path):
    """A YAML file that parses to a non-dict (e.g. a scalar) raises ValidationError.

    Exercises the ``if not isinstance(raw, dict): raw = {}`` branch in
    :func:`core.config.load_config`.
    """
    path = tmp_path / "scalar.yaml"
    path.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_format_summary_renders_ok_line():
    """_format_summary renders the one-line summary printed by the CLI."""
    cfg = load_config(SAMPLE_CONFIG)
    assert _format_summary(cfg) == "OK: loaded 2 rpc_node(s); listen_port=8545"


# ---------------------------------------------------------------------------
# __main__ block — exercised via runpy so coverage tracks the in-process run.
# ---------------------------------------------------------------------------


def test_main_block_success(monkeypatch, capsys):
    """``python -m core.config config.yaml`` prints the OK summary."""
    monkeypatch.setattr(sys, "argv", ["core.config", str(SAMPLE_CONFIG)])
    runpy.run_module("core.config", run_name="__main__")
    captured = capsys.readouterr()
    assert "OK: loaded 2 rpc_node(s); listen_port=8545" in captured.out


def test_main_block_usage_error(monkeypatch, capsys):
    """``python -m core.config`` with no args prints usage and exits 2."""
    monkeypatch.setattr(sys, "argv", ["core.config"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("core.config", run_name="__main__")
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err


def test_main_block_re_raises_on_failure(monkeypatch, capsys, tmp_path):
    """``python -m core.config`` on a missing path prints FAIL and re-raises."""
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(sys, "argv", ["core.config", str(missing)])
    with pytest.raises(FileNotFoundError):
        runpy.run_module("core.config", run_name="__main__")
    captured = capsys.readouterr()
    assert "FAIL:" in captured.err
