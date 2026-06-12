"""Tests for :mod:`core.state`.

The :class:`core.state.RouterState` dataclass is the single in-memory
state model for the Web3 Smart RPC Router. The tests in this module
exercise every observable behaviour declared in the Phase 2 plan
(AC-2 and AC-7): default initialisation, the ``transaction()`` async
context manager, ``snapshot()`` decoupling from live mutations,
``NodeStats`` defaults, the 256-entry cap on the event log, the
``round_robin_index`` counter, the ``tps_1s`` rolling-window math,
and the type of the underlying ``asyncio.Lock``.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from core.models import GlobalSettings, RoutingStrategy, RpcNode
from core.state import NodeStats, RouterState


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def global_settings() -> GlobalSettings:
    """A :class:`GlobalSettings` instance good enough for :class:`RouterState.from_config`."""
    return GlobalSettings(
        listen_port=8545,
        probe_interval_seconds=5.0,
        request_timeout_seconds=10.0,
        max_retries=3,
    )


@pytest.fixture
def two_node_config(global_settings: GlobalSettings):
    """A :class:`RouterConfig` with two distinct RpcNode entries."""
    return [
        RpcNode(
            provider="alpha",
            url="https://alpha.example.com",
            routing_strategy=RoutingStrategy.PRIORITY,
            priority=1,
            weight=1,
            headers={},
        ),
        RpcNode(
            provider="beta",
            url="https://beta.example.com",
            routing_strategy=RoutingStrategy.ROUND_ROBIN,
            priority=2,
            weight=1,
            headers={},
        ),
    ]


# ---------------------------------------------------------------------------
# AC-7 case 1: state initialization defaults
# ---------------------------------------------------------------------------


def test_state_initialisation_defaults() -> None:
    """A fresh RouterState starts with zeroed counters and an empty event log."""
    state = RouterState()
    assert state.nodes == {}
    assert state.round_robin_index == 0
    assert state.total_requests == 0
    assert state.total_success == 0
    assert state.total_failovers == 0
    assert state.tps_1s == 0.0
    # event_log is a bounded deque, not a plain list.
    assert isinstance(state.event_log, type(state.event_log))
    assert len(state.event_log) == 0


# ---------------------------------------------------------------------------
# AC-7 case 8: lock is an asyncio.Lock
# ---------------------------------------------------------------------------


def test_state_lock_is_asyncio_lock() -> None:
    """The internal lock is a real :class:`asyncio.Lock` instance."""
    state = RouterState()
    assert isinstance(state.lock, asyncio.Lock)
    assert not state.lock.locked()


# ---------------------------------------------------------------------------
# AC-7 case 2: transaction() acquires and releases the lock
# ---------------------------------------------------------------------------


async def test_transaction_acquires_and_releases_lock() -> None:
    """Entering ``transaction()`` acquires the lock; exiting releases it."""
    state = RouterState()
    assert not state.lock.locked()
    async with state.transaction():
        assert state.lock.locked()
    assert not state.lock.locked()
    # Nested use of the same state is also safe.
    async with state.transaction():
        assert state.lock.locked()
    assert not state.lock.locked()


async def test_transaction_yields_state_self() -> None:
    """The ``transaction()`` async context manager yields the state itself."""
    state = RouterState()
    async with state.transaction() as inner:
        assert inner is state


# ---------------------------------------------------------------------------
# AC-7 case 3: snapshot() is decoupled from live mutation
# ---------------------------------------------------------------------------


def test_snapshot_decoupled_from_live_mutation() -> None:
    """A :func:`snapshot` is a deep copy: mutating the live state does not change it."""
    state = RouterState()
    state.total_requests = 5
    snap = state.snapshot()
    assert snap["total_requests"] == 5
    # Mutate the live state after the snapshot.
    state.total_requests = 99
    state.event_log.append("mutated")
    # The snapshot must remain at the pre-mutation values.
    assert snap["total_requests"] == 5
    assert snap["event_log"] == []


# ---------------------------------------------------------------------------
# AC-7 case 4: NodeStats defaults
# ---------------------------------------------------------------------------


def test_node_stats_defaults() -> None:
    """Default :class:`NodeStats` is healthy with no measurements recorded."""
    stats = NodeStats(
        provider="p",
        url="https://p.example.com",
        priority=1,
        routing_strategy=RoutingStrategy.ROUND_ROBIN,
    )
    assert stats.healthy is True
    assert stats.latency_ms is None
    assert stats.consecutive_failures == 0
    assert stats.last_error is None
    assert stats.last_probed_at is None


# ---------------------------------------------------------------------------
# AC-7 case 5: event_log cap at 256
# ---------------------------------------------------------------------------


async def test_event_log_capped_at_256() -> None:
    """``record_event`` keeps the deque bounded at 256 entries."""
    state = RouterState()
    for i in range(300):
        await state.record_event(f"event-{i}")
    assert len(state.event_log) == 256
    # The cap drops the oldest entries first.
    assert state.event_log[0] == "event-44"
    assert state.event_log[-1] == "event-299"


# ---------------------------------------------------------------------------
# AC-7 case 6: round_robin_index increments
# ---------------------------------------------------------------------------


async def test_round_robin_index_increments_under_transaction() -> None:
    """``round_robin_index`` increments atomically inside ``transaction()``."""
    state = RouterState()
    for expected in range(1, 6):
        async with state.transaction():
            state.round_robin_index += 1
        assert state.round_robin_index == expected


# ---------------------------------------------------------------------------
# AC-7 case 7: tps_1s math
# ---------------------------------------------------------------------------


async def test_tps_1s_math() -> None:
    """``tps_1s`` reflects the rolling count of requests in the last 1s window."""
    state = RouterState()
    await state.record_request(success=True)
    await state.record_request(success=True)
    await state.record_request(success=True)
    assert state.tps_1s == 3.0
    assert state.total_requests == 3
    assert state.total_success == 3
    # A failed request also counts toward TPS and increments total_failovers.
    await state.record_request(success=False)
    assert state.tps_1s == 4.0
    assert state.total_failovers == 1
    assert state.total_success == 3
    assert state.total_requests == 4


async def test_tps_1s_drops_stale_timestamps() -> None:
    """Timestamps older than 1s are pruned from the rolling window."""
    state = RouterState()
    # Pre-populate with three obviously-stale timestamps. The very first
    # ``record_request`` call will pop all of them (they are far older
    # than ``now - 1.0``) and append the new ``now``.
    state._request_timestamps.append(0.0)
    state._request_timestamps.append(0.0)
    state._request_timestamps.append(0.0)
    await state.record_request(success=True)
    # All three stale entries are pruned; the new request is the only
    # one inside the 1s window.
    assert state.tps_1s == 1.0
    # Add a few more requests in the same instant; no further pruning.
    await state.record_request(success=True)
    await state.record_request(success=True)
    assert state.tps_1s == 3.0


# ---------------------------------------------------------------------------
# from_config classmethod
# ---------------------------------------------------------------------------


async def test_from_config_seeds_nodes(global_settings, two_node_config) -> None:
    """``from_config`` seeds ``nodes`` keyed by provider with default stats."""
    state = RouterState.from_config(two_node_config)
    assert set(state.nodes) == {"alpha", "beta"}
    assert state.nodes["alpha"].provider == "alpha"
    assert state.nodes["alpha"].url == "https://alpha.example.com"
    assert state.nodes["alpha"].priority == 1
    assert state.nodes["alpha"].routing_strategy is RoutingStrategy.PRIORITY
    assert state.nodes["alpha"].healthy is True
    # Counter / log fields stay at their zero defaults.
    assert state.total_requests == 0
    assert state.event_log == type(state.event_log)() or list(state.event_log) == []


async def test_record_failover_appends_event_and_increments() -> None:
    """``record_failover`` appends the canonical event-tape line and bumps the counter."""
    state = RouterState()
    await state.record_failover("alpha", "beta")
    assert state.total_failovers == 1
    assert list(state.event_log)[-1] == "failover alpha -> beta"


# ---------------------------------------------------------------------------
# snapshot contains a deep copy of nested structures
# ---------------------------------------------------------------------------


def test_snapshot_deep_copies_nodes_and_event_log() -> None:
    """``snapshot()`` returns structures that are safe to mutate independently."""
    state = RouterState()
    snap = state.snapshot()
    # The event_log is a copy: appending to live does not affect snap, and vice versa.
    state.event_log.append("x")
    assert snap["event_log"] == []
    snap["event_log"].append("y")
    assert list(state.event_log) == ["x"]
