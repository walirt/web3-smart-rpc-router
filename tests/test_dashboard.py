"""Tests for :mod:`ui.dashboard`.

The dashboard is a read-only :mod:`rich` TUI that snapshots the
:class:`RouterState` once per second and renders a 3-row layout:
a header panel with title/uptime/TPS, a body table with one row per
node, and a footer panel showing the last 8 events. The tests in
this module cover the public surface area declared in AC-10:

* :func:`render_frame` returns a :class:`rich.layout.Layout`.
* The body table has one row per node in the snapshot.
* The event-tape footer shows the most recent lines in order.
* :func:`dashboard_loop` exits when the ``stop`` event is set.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from rich.layout import Layout
from rich.table import Table

from core.models import RoutingStrategy, RpcNode
from core.state import NodeStats, RouterState
from ui.dashboard import dashboard_loop, render_frame


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
    for msg in [
        "failover alpha -> beta",
        "probe-fail beta upstream returned HTTP 503",
        "failover beta -> alpha",
    ]:
        state.event_log.append(msg)
    state.started_at = time.monotonic() - 30.0  # 30s of fake uptime
    return state


# ---------------------------------------------------------------------------
# AC-10 case 1: render_frame returns a Layout
# ---------------------------------------------------------------------------


def test_render_frame_returns_layout(state_for_dashboard: RouterState) -> None:
    """``render_frame`` returns a :class:`rich.layout.Layout` instance."""
    snapshot = state_for_dashboard.snapshot()
    layout = render_frame(snapshot)
    assert isinstance(layout, Layout)


# ---------------------------------------------------------------------------
# AC-10 case 2: the body table has one row per node
# ---------------------------------------------------------------------------


def test_render_frame_table_has_one_row_per_node(
    state_for_dashboard: RouterState,
) -> None:
    """The body table has exactly ``len(nodes)`` rows."""
    snapshot = state_for_dashboard.snapshot()
    layout = render_frame(snapshot)
    # The body is the middle row of the layout split. Walk the layout
    # tree to find the first Table instance and count its data rows.
    tables = list(_walk_tables(layout))
    assert tables, "render_frame produced no Table"
    table = tables[0]
    # Rich Table.row_count is the visible data row count.
    assert table.row_count == len(snapshot["nodes"])


def _walk_tables(node) -> list[Table]:
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


# ---------------------------------------------------------------------------
# AC-10 case 3: event-tape shows the most recent lines in order
# ---------------------------------------------------------------------------


def test_render_frame_event_tape_preserves_order(
    state_for_dashboard: RouterState,
) -> None:
    """The event-tape footer preserves the original ordering of the last N events."""
    snapshot = state_for_dashboard.snapshot()
    layout = render_frame(snapshot)
    rendered_text = _flatten_text(layout)
    events = list(snapshot["event_log"])
    # The last 8 lines of the event log are rendered verbatim and in
    # the same order. We check that every line is present in order.
    last = events[-8:]
    cursor = 0
    for line in last:
        idx = rendered_text.find(line, cursor)
        assert idx >= 0, f"event line {line!r} not found in rendered text"
        cursor = idx + len(line)


def _flatten_text(node) -> str:
    """Render ``node`` via Rich and return the captured text."""
    from io import StringIO
    from rich.console import Console

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    console.print(node)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# AC-10 case 4: dashboard_loop exits on stop event
# ---------------------------------------------------------------------------


async def test_dashboard_loop_exits_when_stop_set(
    state_for_dashboard: RouterState,
) -> None:
    """``dashboard_loop`` returns within one tick after ``stop`` is set."""
    stop = asyncio.Event()
    # Schedule the loop, then set stop almost immediately. The loop
    # should exit before its next refresh tick.
    task = asyncio.create_task(
        dashboard_loop(state_for_dashboard, stop, refresh_seconds=0.05)
    )
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert stop.is_set()
