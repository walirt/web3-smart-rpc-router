"""Schema tests for the Web3 Smart RPC Router configuration contract.

Each test exercises one constraint declared in :mod:`core.models`. The
fixtures from :mod:`tests.conftest` provide minimal valid dicts that
each test copies and mutates to drive the specific validation path it
cares about.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.models import (
    GlobalSettings,
    RoutingStrategy,
    RpcNode,
    RouterConfig,
)


def test_valid_full_config_builds(valid_global_dict, valid_node_dict):
    """A complete, in-range config round-trips through RouterConfig.model_validate."""
    raw = {"global": valid_global_dict, "rpc_nodes": [valid_node_dict]}

    cfg = RouterConfig.model_validate(raw)

    assert cfg.global_.listen_port == 8545
    assert cfg.global_.probe_interval_seconds == 5.0
    assert cfg.global_.request_timeout_seconds == 10.0
    assert cfg.global_.max_retries == 3
    assert len(cfg.rpc_nodes) == 1
    assert cfg.rpc_nodes[0].provider == "test-provider"
    assert cfg.rpc_nodes[0].url == "https://example.com"
    assert cfg.rpc_nodes[0].routing_strategy == RoutingStrategy.ROUND_ROBIN
    assert cfg.rpc_nodes[0].priority == 1
    assert cfg.rpc_nodes[0].weight == 1
    assert cfg.rpc_nodes[0].headers == {}


def test_missing_rpc_nodes_raises(valid_global_dict):
    """Omitting rpc_nodes from the top-level dict is a ValidationError."""
    raw = {"global": valid_global_dict}

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(raw)


def test_empty_rpc_nodes_raises(valid_global_dict):
    """rpc_nodes=[] violates the min_length=1 constraint."""
    raw = {"global": valid_global_dict, "rpc_nodes": []}

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(raw)


def test_missing_provider_raises(valid_node_dict):
    """Omitting provider from a node is a ValidationError."""
    node = dict(valid_node_dict)
    del node["provider"]

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_empty_string_provider_raises(valid_node_dict):
    """provider='' violates the min_length=1 constraint."""
    node = dict(valid_node_dict)
    node["provider"] = ""

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_non_string_provider_raises(valid_node_dict):
    """A non-string provider (e.g. int) hits the else-branch of the strip validator."""
    node = dict(valid_node_dict)
    node["provider"] = 42

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_url_without_scheme_raises(valid_node_dict):
    """A bare hostname with no scheme is rejected by the URL validator."""
    node = dict(valid_node_dict)
    node["url"] = "example.com"

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_url_with_disallowed_scheme_raises(valid_node_dict):
    """ftp:// and other non-http(s) schemes are rejected."""
    node = dict(valid_node_dict)
    node["url"] = "ftp://example.com"

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_url_with_https_accepted(valid_node_dict):
    """A well-formed https:// URL is accepted and stored verbatim."""
    node = dict(valid_node_dict)
    node["url"] = "https://rpc.example.org/eth"

    parsed = RpcNode.model_validate(node)

    assert parsed.url == "https://rpc.example.org/eth"


def test_invalid_routing_strategy_raises(valid_node_dict):
    """A routing_strategy outside the locked enum is rejected."""
    node = dict(valid_node_dict)
    node["routing_strategy"] = "magic"

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_priority_below_one_raises(valid_node_dict):
    """priority=-5 violates the ge=1 constraint."""
    node = dict(valid_node_dict)
    node["priority"] = -5

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


def test_priority_zero_raises(valid_node_dict):
    """priority=0 violates the ge=1 constraint."""
    node = dict(valid_node_dict)
    node["priority"] = 0

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)


@pytest.mark.parametrize("port", [0, 70000])
def test_listen_port_out_of_range_raises(valid_global_dict, port):
    """listen_port=0 is below the minimum; listen_port=70000 is above the maximum."""
    g = dict(valid_global_dict)
    g["listen_port"] = port

    with pytest.raises(ValidationError):
        GlobalSettings.model_validate(g)


@pytest.mark.parametrize("value", [0, -1.5])
def test_probe_interval_nonpositive_raises(valid_global_dict, value):
    """probe_interval_seconds must be > 0; 0 and negative values are rejected."""
    g = dict(valid_global_dict)
    g["probe_interval_seconds"] = value

    with pytest.raises(ValidationError):
        GlobalSettings.model_validate(g)


@pytest.mark.parametrize("value", [0, -2.0])
def test_request_timeout_nonpositive_raises(valid_global_dict, value):
    """request_timeout_seconds must be > 0; 0 and negative values are rejected."""
    g = dict(valid_global_dict)
    g["request_timeout_seconds"] = value

    with pytest.raises(ValidationError):
        GlobalSettings.model_validate(g)


def test_duplicate_provider_raises(valid_global_dict, valid_node_dict):
    """Two nodes sharing a provider label are caught by the model_validator."""
    other = dict(valid_node_dict)
    other["priority"] = 2  # keep priorities unique so the provider check fires first
    raw = {"global": valid_global_dict, "rpc_nodes": [valid_node_dict, other]}

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(raw)


def test_duplicate_priority_raises(valid_global_dict, valid_node_dict):
    """Two nodes sharing a priority value are caught by the model_validator."""
    other = dict(valid_node_dict)
    other["provider"] = "other-provider"  # keep providers unique
    raw = {"global": valid_global_dict, "rpc_nodes": [valid_node_dict, other]}

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(raw)


def test_extra_unknown_top_level_key_rejected(valid_global_dict, valid_node_dict):
    """An unknown key at the RouterConfig level violates extra='forbid'."""
    raw = {
        "global": valid_global_dict,
        "rpc_nodes": [valid_node_dict],
        "bogus_key": 123,
    }

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(raw)


def test_weight_zero_raises(valid_node_dict):
    """weight=0 violates the ge=1 constraint."""
    node = dict(valid_node_dict)
    node["weight"] = 0

    with pytest.raises(ValidationError):
        RpcNode.model_validate(node)
