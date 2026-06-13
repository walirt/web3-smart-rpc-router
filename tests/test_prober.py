"""Tests for :mod:`core.prober`.

The prober is the background health-check engine: once every
``probe_interval_seconds`` it calls ``eth_blockNumber`` on each
configured RPC node, updates the per-node :class:`NodeStats` snapshot
in the shared :class:`RouterState`, and never crashes even if a single
node throws. The tests in this module cover the recording path
(success vs. failure), the loop's tolerance of an exception in one
tick, and the ``stop`` event's ability to break the loop.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aioresponses import aioresponses

from core.models import GlobalSettings, RoutingStrategy, RpcNode
from core.prober import probe_once, prober_loop
from core.state import RouterState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def global_settings() -> GlobalSettings:
    return GlobalSettings(
        listen_port=18545,
        probe_interval_seconds=0.05,  # tight loop for tests
        request_timeout_seconds=2.0,
        routing_strategy=RoutingStrategy.PRIORITY,
    )


@pytest.fixture
def alpha_node() -> RpcNode:
    return RpcNode(
        provider="alpha",
        url="https://alpha.test/rpc",
        priority=1,
        headers={},
    )


@pytest.fixture
def beta_node() -> RpcNode:
    return RpcNode(
        provider="beta",
        url="https://beta.test/rpc",
        priority=2,
        headers={},
    )


@pytest.fixture
def two_node_config(alpha_node: RpcNode, beta_node: RpcNode) -> list[RpcNode]:
    return [alpha_node, beta_node]


@pytest.fixture
def state_for(two_node_config) -> RouterState:
    return RouterState.from_config(two_node_config)


@pytest.fixture
async def aiohttp_session() -> aiohttp.ClientSession:
    async with aiohttp.ClientSession() as session:
        yield session


# ---------------------------------------------------------------------------
# probe_once: success path
# ---------------------------------------------------------------------------


async def test_probe_once_records_success(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """A 200 response from a node is recorded as healthy with a latency."""
    body = {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", payload=body, status=200)
        mocked.post("https://beta.test/rpc", payload=body, status=200)
        await probe_once(state_for, two_node_config, aiohttp_session)
    alpha = state_for.nodes["alpha"]
    beta = state_for.nodes["beta"]
    assert alpha.healthy is True
    assert alpha.consecutive_failures == 0
    assert alpha.last_error is None
    assert alpha.latency_ms is not None
    assert alpha.latency_ms >= 0.0
    assert alpha.last_probed_at is not None
    # beta is identical to alpha for this assertion.
    assert beta.healthy is True
    assert beta.consecutive_failures == 0


# ---------------------------------------------------------------------------
# probe_once: failure path
# ---------------------------------------------------------------------------


async def test_probe_once_records_failure_and_event(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """A 500 response is recorded as unhealthy; the event tape is updated."""
    bad = {"err": "boom"}
    ok = {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", payload=bad, status=500)
        mocked.post("https://beta.test/rpc", payload=ok, status=200)
        await probe_once(state_for, two_node_config, aiohttp_session)
    alpha = state_for.nodes["alpha"]
    assert alpha.healthy is False
    assert alpha.consecutive_failures == 1
    assert alpha.last_error is not None
    assert alpha.latency_ms is None
    assert alpha.last_probed_at is not None
    # The event-tape line mentions the failing provider.
    assert any(
        line.startswith("probe-fail alpha")
        for line in state_for.event_log
    )


# ---------------------------------------------------------------------------
# probe_once: exception isolation
# ---------------------------------------------------------------------------


async def test_probe_once_continues_past_exception(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """An exception on one node must not abort the loop; later nodes are still probed."""
    body = {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
    with aioresponses() as mocked:
        mocked.post("https://alpha.test/rpc", exception=aiohttp.ClientConnectionError("nope"))
        mocked.post("https://beta.test/rpc", payload=body, status=200)
        await probe_once(state_for, two_node_config, aiohttp_session)
    # Alpha should be marked unhealthy; beta healthy.
    assert state_for.nodes["alpha"].healthy is False
    assert state_for.nodes["alpha"].consecutive_failures == 1
    assert state_for.nodes["beta"].healthy is True
    assert state_for.nodes["beta"].consecutive_failures == 0


# ---------------------------------------------------------------------------
# prober_loop: stop signal exits the loop
# ---------------------------------------------------------------------------


async def test_prober_loop_exits_when_stop_set(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """Setting the ``stop`` event terminates the prober loop promptly."""
    with aioresponses() as mocked:
        # We don't know how many ticks will happen; allow plenty of calls.
        for _ in range(10):
            mocked.post(
                "https://alpha.test/rpc",
                payload={"jsonrpc": "2.0", "id": 1, "result": "0x1"},
                status=200,
            )
            mocked.post(
                "https://beta.test/rpc",
                payload={"jsonrpc": "2.0", "id": 1, "result": "0x1"},
                status=200,
            )
        stop = asyncio.Event()
        loop_task = asyncio.create_task(
            prober_loop(state_for, two_node_config, aiohttp_session, stop)
        )
        # Let the first tick complete.
        await asyncio.sleep(0.1)
        stop.set()
        # The loop should exit within one interval.
        await asyncio.wait_for(loop_task, timeout=1.0)
        assert state_for.nodes["alpha"].healthy is True
        assert state_for.nodes["beta"].healthy is True


# ---------------------------------------------------------------------------
# prober_loop: runs multiple ticks
# ---------------------------------------------------------------------------


async def test_prober_loop_runs_multiple_ticks(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """Without a stop signal, the loop runs at least two probe ticks."""
    body = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
    with aioresponses() as mocked:
        # Allow at least four calls (two ticks per node).
        for _ in range(4):
            mocked.post("https://alpha.test/rpc", payload=body, status=200)
            mocked.post("https://beta.test/rpc", payload=body, status=200)
        stop = asyncio.Event()
        loop_task = asyncio.create_task(
            prober_loop(state_for, two_node_config, aiohttp_session, stop)
        )
        # Wait for at least two intervals (interval = 0.05s).
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)
    # The healthy flag should have flipped at least once.
    assert state_for.nodes["alpha"].last_probed_at is not None
    assert state_for.nodes["beta"].last_probed_at is not None
    assert state_for.nodes["alpha"].healthy is True
    assert state_for.nodes["beta"].healthy is True


async def test_prober_loop_tolerates_unexpected_tick_exception(
    monkeypatch: pytest.MonkeyPatch,
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """An unexpected exception in one tick is logged and the loop keeps running."""
    calls = {"count": 0}

    async def broken_probe(*args: object, **kwargs: object) -> None:
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr("core.prober.probe_once", broken_probe)
    stop = asyncio.Event()
    task = asyncio.create_task(
        prober_loop(
            state_for, two_node_config, aiohttp_session, stop,
            probe_interval_seconds=0.05,
        )
    )
    # Two intervals' worth of opportunity for the broken tick to fire.
    await asyncio.sleep(0.12)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert calls["count"] >= 2  # the loop kept going after each exception




async def test_prober_loop_returns_immediately_when_stop_already_set(
    state_for: RouterState,
    two_node_config: list[RpcNode],
    aiohttp_session: aiohttp.ClientSession,
) -> None:
    """A pre-set ``stop`` event causes the loop to return without probing."""
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(
        prober_loop(
            state_for, two_node_config, aiohttp_session, stop,
            probe_interval_seconds=0.05,
        ),
        timeout=1.0,
    )
    assert state_for.nodes["alpha"].last_probed_at is None
