"""Tests for :mod:`ui.dashboard`.

The dashboard is a read-only :mod:`rich` TUI that snapshots the
:class:`RouterState` once per second and renders a five-panel layout:
a header panel, node health, method routing, traffic, and a footer
panel showing the last 8 events. The tests in
this module cover the public surface area declared in AC-10:

* :func:`render_frame` returns a :class:`rich.layout.Layout`.
* The body table has one row per node in the snapshot.
* The event-tape footer shows the most recent lines in order.
* :func:`dashboard_loop` exits when the ``stop`` event is set
  both before and during a refresh tick.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from io import StringIO

import pytest
from rich.console import Console
from rich.layout import Layout
from rich.table import Table

from core.models import RoutingStrategy
from core.state import NodeStats, RouterState
from ui.dashboard import (
    _build_demo_state,
    _build_method_routes,
    _demo_main,
    _format_route_strategy,
    _format_strategy_value,
    _format_log_line,
    _format_status,
    dashboard_loop,
    render_frame,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state_for_dashboard() -> RouterState:
    """A :class:`RouterState` with two nodes and a small event log."""
    state = RouterState()
    state.nodes["alpha"] = NodeStats(
        provider="alpha",
        url="https://alpha.test/rpc",
        priority=1,
        routing_strategy=RoutingStrategy.PRIORITY,
        healthy=True,
        latency_ms=12.5,
        consecutive_failures=0,
    )
    state.nodes["beta"] = NodeStats(
        provider="beta",
        url="https://beta.test/rpc",
        priority=2,
        routing_strategy=RoutingStrategy.FAILOVER,
        healthy=False,
        latency_ms=None,
        consecutive_failures=3,
        last_error="upstream returned HTTP 503",
    )
    state.total_requests = 7
    state.total_success = 5
    state.total_failovers = 2
    state.tps_1s = 1.0
    state.routing_strategy = RoutingStrategy.PRIORITY
    state.listen_port = 8545
    state.method_routes = {
        "eth_getLogs": {
            "providers": ["alpha"],
            "routing_strategy": RoutingStrategy.LOWEST_LATENCY,
        },
        "eth_sendRawTransaction": {
            "providers": ["beta"],
            "routing_strategy": RoutingStrategy.FAILOVER,
        },
    }
    for msg in [
        "failover alpha -> beta",
        "probe-fail beta upstream returned HTTP 503",
        "failover beta -> alpha",
    ]:
        state.event_log.append(msg)
    state.started_at = time.monotonic() - 30.0  # 30s of fake uptime
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_tables(node: object) -> list[Table]:
    """Yield every :class:`Table` reachable from ``node``."""
    found: list[Table] = []
    stack: list[object] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Table):
            found.append(cur)
        children = getattr(cur, "children", None)
        if children:
            for c in children:
                stack.append(c)
        renderable = getattr(cur, "renderable", None)
        if renderable is not None and renderable is not cur:
            stack.append(renderable)
    return found


def _flatten_text(node: object) -> str:
    """Render ``node`` via Rich and return the captured text."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    console.print(node)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# AC-10: render_frame returns a Layout
# ---------------------------------------------------------------------------


def test_render_frame_returns_layout(state_for_dashboard: RouterState) -> None:
    """``render_frame`` returns a :class:`rich.layout.Layout` instance."""
    snapshot = state_for_dashboard.snapshot()
    layout = render_frame(snapshot)
    assert isinstance(layout, Layout)


# ---------------------------------------------------------------------------
# AC-10: the body table has one row per node
# ---------------------------------------------------------------------------


def test_render_frame_table_has_one_row_per_node(
    state_for_dashboard: RouterState,
) -> None:
    """The node and method-routing tables match their snapshot sizes."""
    snapshot = state_for_dashboard.snapshot()
    layout = render_frame(snapshot)
    tables = list(_walk_tables(layout))
    assert tables, "render_frame produced no Table"
    assert any(table.row_count == len(snapshot["nodes"]) for table in tables)
    assert any(table.row_count == len(snapshot["method_routes"]) for table in tables)


# ---------------------------------------------------------------------------
# AC-10: event-tape shows the most recent lines in order
# ---------------------------------------------------------------------------


def test_render_frame_event_tape_preserves_order(
    state_for_dashboard: RouterState,
) -> None:
    """The event-tape footer preserves the original ordering of the last N events."""
    snapshot = state_for_dashboard.snapshot()
    layout = render_frame(snapshot)
    rendered_text = _flatten_text(layout)
    events = list(snapshot["event_log"])
    last = events[-8:]
    cursor = 0
    for line in last:
        expected = line.removeprefix("failover ").removeprefix("probe-fail ")
        idx = rendered_text.find(expected, cursor)
        assert idx >= 0, f"event line {expected!r} not found in rendered text"
        cursor = idx + len(expected)


# ---------------------------------------------------------------------------
# AC-10: render_frame with an empty event log shows the placeholder
# ---------------------------------------------------------------------------


def test_render_frame_handles_empty_event_log() -> None:
    """An empty event log renders the self-healing placeholder."""
    state = RouterState()
    snapshot = state.snapshot()
    layout = render_frame(snapshot)
    text = _flatten_text(layout)
    assert "no self-healing events yet" in text


def test_render_frame_uses_requested_dashboard_labels(
    state_for_dashboard: RouterState,
) -> None:
    """The rendered frame exposes the requested dashboard labels."""
    text = _flatten_text(render_frame(state_for_dashboard.snapshot()))
    assert "Web3 Smart RPC Router (v1.0)" in text
    assert "ROUTING STRATEGY: priority" in text
    assert "Port: 8545" in text
    assert "节点健康(Node Health)" in text
    assert "方法分流(Method Routing)" in text
    assert "全局流量统计" in text
    assert "实时自愈日志" in text
    assert "PROVIDER" in text
    assert "METHOD" in text
    assert "eth_getLogs" in text
    assert "SUCCESS RATE" in text


def test_method_routes_panel_handles_empty_routes() -> None:
    """An empty method-routing snapshot renders the global-strategy placeholder."""
    text = _flatten_text(_build_method_routes({"method_routes": {}}))
    assert "no method-specific routes" in text


def test_route_strategy_formatter_handles_string_and_unknown_values() -> None:
    """Route strategy labels are stable for enum, string, and fallback values."""
    assert _format_route_strategy(RoutingStrategy.FAILOVER) == "Fallback"
    assert _format_route_strategy("round_robin") == "Round Robin"
    assert _format_route_strategy("custom") == "custom"
    assert _format_route_strategy(123) == "123"


def test_global_strategy_formatter_uses_yaml_value() -> None:
    """The header shows the config-facing strategy value."""
    assert _format_strategy_value(RoutingStrategy.PRIORITY) == "priority"
    assert _format_strategy_value("custom") == "custom"


def test_status_formatter_handles_429_and_generic_error() -> None:
    """Status labels distinguish rate-limit degradation from generic errors."""
    rate_limited = NodeStats(
        provider="rl",
        url="https://rl.test",
        priority=1,
        routing_strategy=RoutingStrategy.PRIORITY,
        healthy=False,
        latency_ms=120.0,
        last_error="upstream returned HTTP 429",
    )
    generic = NodeStats(
        provider="generic",
        url="https://generic.test",
        priority=2,
        routing_strategy=RoutingStrategy.PRIORITY,
        healthy=False,
        latency_ms=20.0,
        last_error="connection reset",
    )
    assert "429" in _format_status(rate_limited)
    assert "ERR" in _format_status(generic)


def test_log_formatter_handles_plain_info_line() -> None:
    """Plain event lines render as INFO rows."""
    assert "[INFO]" in _format_log_line("Request eth_call -> alpha (48ms)")


# ---------------------------------------------------------------------------
# AC-10: dashboard_loop exits on stop (during a tick)
# ---------------------------------------------------------------------------


async def test_dashboard_loop_exits_when_stop_set(
    state_for_dashboard: RouterState,
) -> None:
    """``dashboard_loop`` returns within one tick after ``stop`` is set."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        dashboard_loop(state_for_dashboard, stop, refresh_seconds=0.05)
    )
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert stop.is_set()


# ---------------------------------------------------------------------------
# dashboard_loop: stop set before loop start -> while branch never entered
# ---------------------------------------------------------------------------


async def test_dashboard_loop_returns_immediately_when_stop_already_set(
    state_for_dashboard: RouterState,
) -> None:
    """A pre-set ``stop`` event causes the loop to return without rendering."""
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(
        dashboard_loop(state_for_dashboard, stop, refresh_seconds=0.05),
        timeout=1.0,
    )


# ---------------------------------------------------------------------------
# dashboard_loop: stop set during the wait -> else: return path
# ---------------------------------------------------------------------------


async def test_dashboard_loop_exercises_except_branch(
    state_for_dashboard: RouterState,
) -> None:
    """When ``wait_for`` times out (no ``stop``), the loop hits the ``except: continue`` path."""
    stop = asyncio.Event()
    # Tight refresh so wait_for times out almost immediately.
    task = asyncio.create_task(
        dashboard_loop(state_for_dashboard, stop, refresh_seconds=0.05)
    )
    # Let two refresh intervals elapse so the loop times out at least
    # twice (which exercises the ``except: continue`` path).
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


async def test_dashboard_loop_returns_via_else_branch_when_stop_set_during_wait(
    state_for_dashboard: RouterState,
) -> None:
    """``stop.set()`` during the wait tick returns through the ``else: return`` path."""
    stop = asyncio.Event()
    # Use a very long refresh so the loop is firmly in the first wait_for.
    task = asyncio.create_task(
        dashboard_loop(state_for_dashboard, stop, refresh_seconds=60.0)
    )
    # Wait long enough for the loop to definitely reach the wait_for.
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# _build_demo_state and _demo_main
# ---------------------------------------------------------------------------


def test_build_demo_state_populates_two_nodes() -> None:
    """``_build_demo_state`` returns a state matching the sample dashboard."""
    state = _build_demo_state()
    assert set(state.nodes) == {"Alchemy-Free", "Infura-Main", "QuickNode", "Local-Node"}
    assert state.routing_strategy is RoutingStrategy.PRIORITY
    assert state.listen_port == 8545
    assert set(state.method_routes) == {"eth_getLogs", "eth_sendRawTransaction"}
    assert len(state.event_log) >= 1


def test_demo_main_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_demo_main`` swallows ``KeyboardInterrupt`` and exits cleanly."""

    async def raise_keyboard_interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("ui.dashboard.dashboard_loop", raise_keyboard_interrupt)
    # Must not raise.
    _demo_main()


def test_dashboard_main_block_invokes_demo_main() -> None:
    """The ``__main__`` block dispatches to :func:`_demo_main`."""
    from ui import dashboard as dashboard_module

    source = inspect.getsource(dashboard_module)
    marker = "if __name__"
    body = source[source.index(marker):]
    namespace: dict[str, object] = {"__name__": "__main__"}
    called = {"count": 0}

    def fake_demo() -> None:
        called["count"] += 1

    namespace["_demo_main"] = fake_demo
    exec(compile(body, dashboard_module.__file__, "exec"), namespace)
    assert called["count"] == 1
