"""Cyberpunk-styled :mod:`rich` TUI dashboard for the Web3 Smart RPC Router.

The dashboard is a *read-only* observer: it calls
:func:`core.state.RouterState.snapshot` once per refresh tick and
renders the result in a 3-row :class:`rich.layout.Layout` (header /
body / footer). It must never mutate the live :class:`RouterState`
and must never acquire its :attr:`~core.state.RouterState.lock`.

The module is invokable as ``python -m ui.dashboard`` for a
standalone demo against a faked :class:`RouterState`; the router's
:class:`core.router.main_async` entry point schedules
:func:`dashboard_loop` as a sibling :class:`asyncio.Task` when
``--with-tui`` is passed.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from core.models import RoutingStrategy
from core.state import NodeStats, RouterState


# ---------------------------------------------------------------------------
# Cyberpunk color palette
# ---------------------------------------------------------------------------

COLOR_BG = "#0a0a14"          # near-black background
COLOR_NEON_GREEN = "#00ff9c"  # healthy / success
COLOR_MAGENTA = "#ff2bd6"     # unhealthy / accent
COLOR_CYAN = "#7df9ff"        # info / title
COLOR_DIM = "#666666"         # secondary text

# Number of events rendered in the footer.
EVENT_TAPE_LINES = 8


# ---------------------------------------------------------------------------
# Pure rendering function (no I/O, safe to test)
# ---------------------------------------------------------------------------


def _format_strategy(strategy: RoutingStrategy) -> str:
    """Render a routing strategy as a short, uppercase label."""
    return strategy.name


def render_frame(snapshot: dict[str, Any]) -> Layout:
    """Build the 3-row dashboard layout for one snapshot of the router state.

    The returned :class:`rich.layout.Layout` is fully self-contained and
    carries no live references back to the source :class:`RouterState`,
    which is why the input is a ``snapshot`` (a ``copy.deepcopy``) and
    not the live state itself.
    """
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=EVENT_TAPE_LINES + 2),
    )
    layout["header"].update(_build_header(snapshot))
    layout["body"].update(_build_body(snapshot))
    layout["footer"].update(_build_footer(snapshot))
    return layout


def _build_header(snapshot: dict[str, Any]) -> Panel:
    """Build the header panel: title, uptime, TPS."""
    uptime = max(0.0, time.monotonic() - float(snapshot.get("started_at", time.monotonic())))
    tps = float(snapshot.get("tps_1s", 0.0))
    total_requests = int(snapshot.get("total_requests", 0))
    total_success = int(snapshot.get("total_success", 0))
    total_failovers = int(snapshot.get("total_failovers", 0))
    title = (
        f"[bold {COLOR_CYAN}]WEB3 SMART RPC ROUTER[/]  "
        f"[{COLOR_NEON_GREEN}]uptime {uptime:6.1f}s[/]  "
        f"[{COLOR_NEON_GREEN}]TPS(1s) {tps:5.2f}[/]  "
        f"[{COLOR_MAGENTA}]req {total_requests}[/]  "
        f"[{COLOR_NEON_GREEN}]ok {total_success}[/]  "
        f"[{COLOR_MAGENTA}]failover {total_failovers}[/]"
    )
    return Panel(
        title,
        border_style=COLOR_MAGENTA,
        style=f"on {COLOR_BG}",
    )


def _build_body(snapshot: dict[str, Any]) -> Table:
    """Build the body table — one row per node."""
    table = Table(
        expand=True,
        show_lines=False,
        border_style=COLOR_CYAN,
        header_style=f"bold {COLOR_CYAN}",
    )
    table.add_column("Provider", style="bold")
    table.add_column("URL", style=COLOR_DIM, overflow="fold")
    table.add_column("Pri", justify="right")
    table.add_column("Strategy")
    table.add_column("Healthy", justify="center")
    table.add_column("Latency(ms)", justify="right")
    table.add_column("ConsecFail", justify="right")
    table.add_column("LastError", style=COLOR_MAGENTA, overflow="fold")

    nodes: dict[str, NodeStats] = snapshot.get("nodes", {})
    # Sort by priority, then by provider name for stability.
    for provider in sorted(nodes, key=lambda p: (nodes[p].priority, p)):
        stats = nodes[provider]
        healthy_text = (
            f"[{COLOR_NEON_GREEN}]YES[/]"
            if stats.healthy
            else f"[{COLOR_MAGENTA}]NO[/]"
        )
        latency = (
            f"{stats.latency_ms:7.1f}"
            if stats.latency_ms is not None
            else "    n/a"
        )
        table.add_row(
            provider,
            stats.url,
            str(stats.priority),
            _format_strategy(stats.routing_strategy),
            healthy_text,
            latency,
            str(stats.consecutive_failures),
            stats.last_error or "",
        )
    return table


def _build_footer(snapshot: dict[str, Any]) -> Panel:
    """Build the footer event-tape panel."""
    events = list(snapshot.get("event_log", []))[-EVENT_TAPE_LINES:]
    if not events:
        body = "[dim](no events yet)[/]"
    else:
        body = "\n".join(f"[{COLOR_NEON_GREEN}]{line}[/]" for line in events)
    return Panel(
        body,
        title="Live event tape",
        title_align="left",
        border_style=COLOR_NEON_GREEN,
        style=f"on {COLOR_BG}",
    )


# ---------------------------------------------------------------------------
# Async dashboard loop
# ---------------------------------------------------------------------------


async def dashboard_loop(
    state: RouterState,
    stop: asyncio.Event,
    *,
    refresh_seconds: float = 1.0,
) -> None:
    """Refresh the TUI once per ``refresh_seconds`` until ``stop`` is set.

    The loop is intentionally small: it calls :meth:`RouterState.snapshot`
    (the only legal way for a reader to observe state) and feeds the
    resulting ``dict`` to :func:`render_frame`. The :class:`rich.live.Live`
    context manager handles the terminal redraw.
    """
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        width=120,
    )
    with Live(
        render_frame(state.snapshot()),
        console=console,
        refresh_per_second=max(1, int(1 / max(refresh_seconds, 0.01))),
        screen=False,
    ) as live:
        while not stop.is_set():
            live.update(render_frame(state.snapshot()))
            try:
                await asyncio.wait_for(stop.wait(), timeout=refresh_seconds)
            except asyncio.TimeoutError:
                continue
            else:
                return


# ---------------------------------------------------------------------------
# Standalone demo (python -m ui.dashboard)
# ---------------------------------------------------------------------------


def _build_demo_state() -> RouterState:
    """Build a fake :class:`RouterState` for the standalone demo."""
    state = RouterState()
    state.nodes["cloudflare-eth"] = NodeStats(
        provider="cloudflare-eth",
        url="https://cloudflare-ethereum.com",
        priority=1,
        routing_strategy=RoutingStrategy.PRIORITY,
        healthy=True,
        latency_ms=42.0,
        consecutive_failures=0,
    )
    state.nodes["ankr-eth"] = NodeStats(
        provider="ankr-eth",
        url="http://rpc.ankr.com/eth",
        priority=2,
        routing_strategy=RoutingStrategy.FAILOVER,
        healthy=False,
        latency_ms=None,
        consecutive_failures=2,
        last_error="ClientConnectionError: connection reset",
    )
    state.total_requests = 1234
    state.total_success = 1200
    state.total_failovers = 7
    state.tps_1s = 3.2
    state.event_log.append("failover cloudflare-eth -> ankr-eth")
    state.event_log.append("probe-fail ankr-eth ClientConnectionError: connection reset")
    state.event_log.append("failover ankr-eth -> cloudflare-eth")
    return state


def _demo_main() -> None:
    """Run the standalone TUI demo until ``Ctrl+C`` is pressed."""
    state = _build_demo_state()
    stop = asyncio.Event()
    try:
        asyncio.run(dashboard_loop(state, stop, refresh_seconds=0.5))
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":  # pragma: no cover - exercised via __main__ test
    _demo_main()


__all__ = ["dashboard_loop", "render_frame"]
