"""Tests for :mod:`core.router`.

The router module exposes:

* :class:`NoHealthyNodeError` — the exception raised when every
  candidate node has been tried without a 2xx response.
* :func:`select_node` — strategy-aware node selection driven by the
  routing strategy declared on each :class:`~core.models.RpcNode`.
* :func:`forward_with_failover` — the core proxy loop: try each node
  in priority order, fall back on 429 / 5xx / network / JSON errors,
  with bounded exponential backoff between attempts.
* :class:`ProxyHandler` and :func:`make_app` — the aiohttp web glue
  that exposes ``POST /`` (JSON-RPC passthrough) and
  ``GET /healthz`` (liveness probe).
* :func:`main_async` — the entry point that ties together the server,
  the prober, and the optional TUI.

Every HTTP-level test in this file uses :mod:`aioresponses` to stub
upstream traffic; no real network calls are made.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aioresponses import aioresponses

from core.config import load_config
from core.models import GlobalSettings, RoutingStrategy, RpcNode
from core.router import (
    NoHealthyNodeError,
    ProxyHandler,
    forward_with_failover,
    main_async,
    make_app,
    select_node,
)
from core.state import RouterState


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_global() -> GlobalSettings:
    """A :class:`GlobalSettings` instance with a 10s request timeout."""
    return GlobalSettings(
        listen_port=18545,
        probe_interval_seconds=60.0,
        request_timeout_seconds=10.0,
        max_retries=3,
    )


@pytest.fixture
def alpha_node() -> RpcNode:
    return RpcNode(
        provider="alpha",
        url="https://alpha.test/rpc",
        routing_strategy=RoutingStrategy.PRIORITY,
        priority=1,
        weight=1,
        headers={},
    )


@pytest.fixture
def beta_node() -> RpcNode:
    return RpcNode(
        provider="beta",
        url="https://beta.test/rpc",
        routing_strategy=RoutingStrategy.PRIORITY,
        priority=2,
        weight=1,
        headers={},
    )


@pytest.fixture
def two_node_config(base_global: GlobalSettings, alpha_node: RpcNode, beta_node: RpcNode) -> list[RpcNode]:
    return [alpha_node, beta_node]


@pytest.fixture
def state_for(two_node_config) -> RouterState:
    """A :class:`RouterState` seeded from the two-node config."""
    return RouterState.from_config(two_node_config)


@pytest.fixture
async def aiohttp_session() -> AsyncIterator[aiohttp.ClientSession]:
    """A real :class:`aiohttp.ClientSession` that uses aioresponses for upstreams."""
    async with aiohttp.ClientSession() as session:
        yield session


# ---------------------------------------------------------------------------
# (a) Happy path: one healthy node echoes the response.
# ---------------------------------------------------------------------------


async def test_happy_path_one_healthy_node_echoes_response(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", payload=body, status=200)
        result, provider = await forward_with_failover(
            state_for, two_node_config, aiohttp_session,
            {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
            request_timeout_seconds=10.0,
        )
    assert result == body
    assert provider == "alpha"
    assert state_for.total_success == 1
    assert state_for.total_requests == 1
    assert state_for.tps_1s >= 0.0


# ---------------------------------------------------------------------------
# (b) 429 on first node + 200 on second -> failover event recorded.
# ---------------------------------------------------------------------------


async def test_429_on_first_node_triggers_failover_to_second(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    body_a = {"error": "rate limited"}
    body_b = {"jsonrpc": "2.0", "id": 1, "result": "0x2"}
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", payload=body_a, status=429)
        mocked.post("https://beta.test/rpc", payload=body_b, status=200)
        result, provider = await forward_with_failover(
            state_for, two_node_config, aiohttp_session,
            {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
            request_timeout_seconds=10.0,
        )
    assert result == body_b
    assert provider == "beta"
    assert state_for.total_success == 1
    assert state_for.total_failovers == 1
    assert "failover alpha -> beta" in list(state_for.event_log)


# ---------------------------------------------------------------------------
# (c) 503 on all nodes -> NoHealthyNodeError and total_failovers >= n-1.
# ---------------------------------------------------------------------------


async def test_503_on_all_nodes_raises_no_healthy(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", payload={"err": "x"}, status=503)
        mocked.post("https://beta.test/rpc", payload={"err": "x"}, status=503)
        with pytest.raises(NoHealthyNodeError):
            await forward_with_failover(
                state_for, two_node_config, aiohttp_session,
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                request_timeout_seconds=10.0,
            )
    # n=2 nodes -> at least n-1 = 1 failover event recorded.
    assert state_for.total_failovers >= 1


# ---------------------------------------------------------------------------
# (d) ClientConnectionError on first node + 200 on second.
# ---------------------------------------------------------------------------


async def test_connection_error_on_first_node_triggers_failover(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "result": "0x3"}
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", exception=aiohttp.ClientConnectionError("nope"))
        mocked.post("https://beta.test/rpc", payload=body, status=200)
        result, provider = await forward_with_failover(
            state_for, two_node_config, aiohttp_session,
            {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
            request_timeout_seconds=10.0,
        )
    assert result == body
    assert provider == "beta"
    assert state_for.total_failovers == 1


# ---------------------------------------------------------------------------
# (e) lowest_latency picks the smallest latency_ms.
# ---------------------------------------------------------------------------


async def test_select_node_lowest_latency(
    state_for: RouterState,
) -> None:
    state_for.nodes["alpha"].latency_ms = 50.0
    state_for.nodes["beta"].latency_ms = 12.0
    cfg = [state_for.nodes["alpha"], state_for.nodes["beta"]]
    # Force both nodes to use the lowest_latency strategy.
    for n in cfg:
        n.routing_strategy = RoutingStrategy.LOWEST_LATENCY
    chosen = await _select(state_for, cfg)
    assert chosen.provider == "beta"


async def _select(state: RouterState, cfg: list[RpcNode]) -> RpcNode:
    """Helper to call select_node under the state's lock where it expects to be."""
    # The router's select_node uses ``state.transaction`` to mutate
    # round_robin_index; calling it directly is fine for non-RR
    # strategies.
    return select_node(state, cfg)


# ---------------------------------------------------------------------------
# (f) round_robin rotates across two healthy nodes over three calls.
# ---------------------------------------------------------------------------


async def test_round_robin_rotates_across_healthy_nodes(
    state_for: RouterState,
) -> None:
    for n in state_for.nodes.values():
        n.routing_strategy = RoutingStrategy.ROUND_ROBIN
    cfg = list(state_for.nodes.values())
    p1 = select_node(state_for, cfg)
    p2 = select_node(state_for, cfg)
    p3 = select_node(state_for, cfg)
    assert p1.provider != p2.provider
    assert p1.provider == p3.provider


# ---------------------------------------------------------------------------
# (g) priority always picks priority=1 when healthy.
# ---------------------------------------------------------------------------


async def test_priority_picks_highest_priority_when_healthy(
    state_for: RouterState,
) -> None:
    # alpha has priority=1 (highest), beta has priority=2.
    chosen = select_node(state_for, list(state_for.nodes.values()))
    assert chosen.provider == "alpha"


# ---------------------------------------------------------------------------
# (h) failover is order-stable across calls (alpha picked every time).
# ---------------------------------------------------------------------------


async def test_failover_is_order_stable(
    state_for: RouterState,
) -> None:
    for n in state_for.nodes.values():
        n.routing_strategy = RoutingStrategy.FAILOVER
    cfg = list(state_for.nodes.values())
    providers = [select_node(state_for, cfg).provider for _ in range(3)]
    assert providers == ["alpha", "alpha", "alpha"]


# ---------------------------------------------------------------------------
# (i) GET /healthz returns 200 {"ok": True}.
# ---------------------------------------------------------------------------


async def test_get_healthz_returns_ok(
    state_for: RouterState,
    two_node_config: list[RpcNode],
) -> None:
    cfg: list[RpcNode] = two_node_config
    app = make_app(state_for, cfg)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/healthz")
        assert resp.status == 200
        payload = await resp.json()
        assert payload == {"ok": True}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# (j) malformed upstream JSON (not a mapping) is treated as failure and retried.
# ---------------------------------------------------------------------------


async def test_malformed_upstream_body_treated_as_failure(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    body_b = {"jsonrpc": "2.0", "id": 1, "result": "0x4"}
    with aioresponses() as mocked:
        # First upstream returns a non-mapping JSON body (an array).
        mocked.post("https://alpha.test/rpc", payload=[1, 2, 3], status=200)
        mocked.post("https://beta.test/rpc", payload=body_b, status=200)
        result, provider = await forward_with_failover(
            state_for, two_node_config, aiohttp_session,
            {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
            request_timeout_seconds=10.0,
        )
    assert result == body_b
    assert provider == "beta"
    assert state_for.total_failovers == 1


# ---------------------------------------------------------------------------
# (k) select_node raises NoHealthyNodeError when all nodes are unhealthy.
# ---------------------------------------------------------------------------


def test_select_node_raises_when_all_unhealthy(
    state_for: RouterState,
) -> None:
    for n in state_for.nodes.values():
        n.healthy = False
    with pytest.raises(NoHealthyNodeError):
        select_node(state_for, list(state_for.nodes.values()))


# ---------------------------------------------------------------------------
# (l) exponential backoff uses base, base*2, base*4 ... bounded by cap.
# ---------------------------------------------------------------------------


async def test_exponential_backoff_schedule(
    monkeypatch: pytest.MonkeyPatch,
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("core.router.asyncio.sleep", fake_sleep)
    with aioresponses() as mocked:
        # All nodes fail; three attempts means two sleeps.
        mocked.post("https://alpha.test/rpc", payload={"err": "x"}, status=503)
        mocked.post("https://beta.test/rpc", payload={"err": "x"}, status=503)
        with pytest.raises(NoHealthyNodeError):
            await forward_with_failover(
                state_for, two_node_config, aiohttp_session,
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                request_timeout_seconds=10.0,
            )
    base = 10.0 / 4  # 2.5
    cap = 10.0 * 4   # 40.0
    expected = [min(base * (2 ** i), cap) for i in range(len(sleeps))]
    assert sleeps == expected
    # The cap must be respected: no delay may exceed cap.
    assert all(s <= cap for s in sleeps)


# ---------------------------------------------------------------------------
# Bonus: ProxyHandler returns 400 on non-mapping JSON-RPC payloads.
# ---------------------------------------------------------------------------


async def test_proxy_handler_rejects_non_mapping_payload(
    state_for: RouterState,
    two_node_config: list[RpcNode],
) -> None:
    cfg: list[RpcNode] = two_node_config
    app = make_app(state_for, cfg)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/", json=[1, 2, 3])
        assert resp.status == 400
        payload = await resp.json()
        assert payload["error"] == "invalid jsonrpc"
    finally:
        await client.close()


async def test_proxy_handler_returns_503_on_no_healthy(
    state_for: RouterState,
    two_node_config: list[RpcNode],
) -> None:
    """All nodes unhealthy + mocked 503s -> handler returns ``503 no healthy upstream``."""
    cfg: list[RpcNode] = two_node_config
    # Mark all nodes unhealthy. The proxy's self-healing fallback still
    # tries them all (with the mocked 503s), and surfaces 503 to the
    # caller when every hop has failed.
    for n in state_for.nodes.values():
        n.healthy = False
    # Passthrough localhost so the TestClient's own POST / reaches the
    # in-process app server instead of being intercepted by aioresponses.
    with aioresponses(passthrough=["http://127.0.0.1"]) as mocked:
        mocked.post("https://alpha.test/rpc", payload={"err": "x"}, status=503)
        mocked.post("https://beta.test/rpc", payload={"err": "x"}, status=503)
        async with aiohttp.ClientSession() as upstream:
            app = make_app(
                state_for, cfg,
                upstream_client=upstream,
                request_timeout_seconds=10.0,
            )
            server = TestServer(app)
            client = TestClient(server)
            await client.start_server()
            try:
                resp = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "x"})
                assert resp.status == 503
                payload = await resp.json()
                assert payload["error"] == "no healthy upstream"
            finally:
                await client.close()
