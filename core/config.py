"""YAML loader for the Web3 Smart RPC Router configuration.

The two public functions in this module — :func:`load_config` and
:func:`parse_config_dict` — are the only supported way to turn YAML
bytes into a validated :class:`~core.models.RouterConfig`. Both raise
on bad input rather than silently coerce.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from core.models import RouterConfig


def parse_config_dict(raw: dict[str, Any]) -> RouterConfig:
    """Validate a raw config dict and return a :class:`RouterConfig`.

    The function is pure: given the same input, it returns a
    structurally identical :class:`RouterConfig` on every call. Pydantic
    validation errors (unknown keys, missing fields, out-of-range
    numbers, duplicate providers, etc.) propagate untouched so callers
    can decide how to surface them.
    """
    return RouterConfig.model_validate(raw)


def load_config(path: str | Path) -> RouterConfig:
    """Read YAML from ``path`` and return a validated :class:`RouterConfig`.

    The file is read as UTF-8 and parsed with :func:`yaml.safe_load`;
    syntax errors propagate as :class:`yaml.YAMLError`. The resulting
    mapping is then handed to :func:`parse_config_dict`, which is
    where pydantic validation kicks in.
    """
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    # A YAML document that is not a mapping (e.g. a scalar, a list, or
    # an empty file) cannot be a valid router config; coerce to an
    # empty mapping and let pydantic emit the usual "field required"
    # error.
    if not isinstance(raw, dict):
        raw = {}
    return parse_config_dict(raw)


def _format_summary(config: RouterConfig) -> str:
    """Render the one-line summary printed by the ``__main__`` block."""
    return (
        f"OK: loaded {len(config.rpc_nodes)} rpc_node(s); "
        f"listen_host={config.global_.listen_host}; "
        f"listen_port={config.global_.listen_port}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "usage: python -m core.config <path-to-config.yaml>",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        cfg = load_config(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 - top-level CLI handler
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    print(_format_summary(cfg))
