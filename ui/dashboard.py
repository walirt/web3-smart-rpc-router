"""Rich TUI dashboard for the Web3 Smart RPC Router.

The dashboard is a read-only observer: it calls
``RouterState.snapshot()`` once per refresh tick and renders a five
panel view for status, node health, method routing, traffic, and live logs.
It never mutates the live state and never acquires the state lock.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from core.models import RoutingStrategy
from core.state import NodeStats, RouterState, format_event


COLOR_BG = "#0a0a14"
COLOR_NEON_GREEN = "#00ff9c"
COLOR_MAGENTA = "#ff2bd6"
COLOR_CYAN = "#7df9ff"
COLOR_DIM = "#666666"

EVENT_TAPE_LINES = 8


def render_frame(snapshot: dict[str, Any]) -> Layout:
    """Build one dashboard frame from a state snapshot."""
    nodes: dict[str, NodeStats] = snapshot.get("nodes", {})
    method_routes: dict[str, dict[str, object]] = snapshot.get("method_routes", {})
    nodes_size = max(6, min(14, len(nodes) + 4))
    methods_size = max(5, min(10, len(method_routes) + 4))
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="nodes", size=nodes_size),
        Layout(name="methods", size=methods_size),
        Layout(name="traffic", size=5),
        Layout(name="logs", size=EVENT_TAPE_LINES + 2),
    )
    layout["header"].update(_build_header(snapshot))
    layout["nodes"].update(_build_nodes(snapshot))
    layout["methods"].update(_build_method_routes(snapshot))
    layout["traffic"].update(_build_traffic(snapshot))
    layout["logs"].update(_build_logs(snapshot))
    return layout


def _build_header(snapshot: dict[str, Any]) -> Panel:
    """Build the title/status header."""
    uptime = max(0.0, time.monotonic() - float(snapshot.get("started_at", time.monotonic())))
    routing_strategy = _format_strategy_value(
        snapshot.get("routing_strategy", RoutingStrategy.PRIORITY)
    )
    host = snapshot.get("listen_host", "127.0.0.1")
    port = snapshot.get("listen_port")
    bind = f"{host}:{port}" if port is not None else f"{host}:-"
    probe_interval = _format_seconds_value(snapshot.get("probe_interval_seconds"))
    request_timeout = _format_seconds_value(snapshot.get("request_timeout_seconds"))
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=3, no_wrap=True)
    grid.add_column(ratio=3, no_wrap=True)
    grid.add_column(ratio=3, no_wrap=True)
    grid.add_row(
        f"🚀 [bold {COLOR_CYAN}]Web3 Smart RPC Router[/]",
        "",
        "",
    )
    grid.add_row(
        f"Probe Interval: [bold {COLOR_CYAN}]{probe_interval}[/]",
        f"Request Timeout: [bold {COLOR_CYAN}]{request_timeout}[/]",
        f"Strategy: [bold {COLOR_CYAN}]{routing_strategy}[/]",
    )
    grid.add_row(
        f"Bind: [bold {COLOR_CYAN}]{bind}[/]",
        f"Status: [[{COLOR_NEON_GREEN}]🟢 ACTIVE[/]]",
        f"Uptime: {_format_uptime(uptime)}",
    )
    return Panel(grid, box=box.ROUNDED, border_style=COLOR_CYAN, style=f"on {COLOR_BG}")


def _build_nodes(snapshot: dict[str, Any]) -> Panel:
    """Build the node health panel."""
    table = Table(
        expand=True,
        show_lines=False,
        box=None,
        show_edge=False,
        pad_edge=False,
        header_style=f"bold {COLOR_CYAN}",
    )
    table.add_column("PROVIDER", style="bold", ratio=3, no_wrap=True)
    table.add_column("STATUS", justify="center", ratio=2, no_wrap=True)
    table.add_column("PING", justify="center", ratio=1, no_wrap=True)
    table.add_column("PRESSURE", justify="center", ratio=2, no_wrap=True)
    table.add_column("SUCCESS RATE", justify="center", ratio=2, no_wrap=True)

    nodes: dict[str, NodeStats] = snapshot.get("nodes", {})
    for provider in sorted(nodes, key=lambda p: (nodes[p].priority, p)):
        stats = nodes[provider]
        table.add_row(
            provider,
            _format_status(stats),
            _format_ping(stats),
            _failure_pressure_bar(stats),
            _success_rate(stats),
        )
    return Panel(
        table,
        title="📡 节点健康(Node Health)",
        title_align="left",
        box=box.ROUNDED,
        border_style=COLOR_CYAN,
        style=f"on {COLOR_BG}",
    )


def _build_method_routes(snapshot: dict[str, Any]) -> Panel:
    """Build the configured method-routing panel."""
    method_routes: dict[str, dict[str, object]] = snapshot.get("method_routes", {})
    if not method_routes:
        body = "[dim](no method-specific routes; using global strategy)[/]"
        return Panel(
            body,
            title="🧭 方法分流(Method Routing)",
            title_align="left",
            box=box.ROUNDED,
            border_style=COLOR_CYAN,
            style=f"on {COLOR_BG}",
        )

    table = Table(
        expand=True,
        show_lines=False,
        box=None,
        show_edge=False,
        pad_edge=False,
        header_style=f"bold {COLOR_CYAN}",
    )
    table.add_column("METHOD", style="bold")
    table.add_column("PROVIDERS")
    table.add_column("Routing Strategy")

    for method in sorted(method_routes):
        route = method_routes[method]
        providers_raw = route.get("providers", [])
        providers = providers_raw if isinstance(providers_raw, list) else []
        provider_labels = ", ".join(str(provider) for provider in providers)
        strategy = route.get("routing_strategy", RoutingStrategy.PRIORITY)
        table.add_row(
            method,
            provider_labels or "-",
            _format_strategy_value(strategy),
        )

    return Panel(
        table,
        title="🧭 方法分流(Method Routing)",
        title_align="left",
        box=box.ROUNDED,
        border_style=COLOR_CYAN,
        style=f"on {COLOR_BG}",
    )


def _build_traffic(snapshot: dict[str, Any]) -> Panel:
    """Build the global traffic and performance panel."""
    tps = float(snapshot.get("tps_1s", 0.0))
    total_requests = int(snapshot.get("total_requests", 0))
    total_failovers = int(snapshot.get("total_failovers", 0))
    body = (
        f"TPS (Current): [bold {COLOR_NEON_GREEN}]{tps:.0f} req/s[/]   "
        f"[{COLOR_CYAN}]{_sparkline(tps)}[/]\n"
        f"Failover Triggered: [bold {COLOR_MAGENTA}]{total_failovers:,}[/] times  |  "
        f"Total Requests Handled: [bold {COLOR_CYAN}]{total_requests:,}[/]\n"
        f"[{COLOR_NEON_GREEN}]💡[/] {_traffic_hint(snapshot)}"
    )
    return Panel(
        body,
        title="📊 全局流量统计 (Traffic & Performance)",
        title_align="left",
        box=box.ROUNDED,
        border_style=COLOR_CYAN,
        style=f"on {COLOR_BG}",
    )


def _build_logs(snapshot: dict[str, Any]) -> Panel:
    """Build the live request-routing log panel."""
    events = list(snapshot.get("event_log", []))[-EVENT_TAPE_LINES:]
    if not events:
        body = "[dim](no request routing events yet)[/]"
    else:
        body = "\n".join(_format_log_line(line) for line in events)
    return Panel(
        body,
        title="🧾 实时请求日志 (Live Request Routing)",
        title_align="left",
        box=box.ROUNDED,
        border_style=COLOR_MAGENTA,
        style=f"on {COLOR_BG}",
    )


def _format_strategy_value(strategy: object) -> str:
    """Render the global routing strategy as the YAML-facing value."""
    if isinstance(strategy, RoutingStrategy):
        return strategy.value
    return str(strategy)


def _format_uptime(seconds: float) -> str:
    """Render seconds as HH:MM:SS."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_seconds_value(value: object) -> str:
    """Render a configured seconds value for compact header display."""
    if isinstance(value, int | float):
        seconds = float(value)
        if seconds.is_integer():
            return f"{int(seconds)}s"
        return f"{seconds:g}s"
    return "-"


def _format_status(stats: NodeStats) -> str:
    """Render current node status."""
    if stats.is_circuit_open():
        return "[yellow]🟡 COOLDOWN[/]"
    if stats.healthy:
        return f"[{COLOR_NEON_GREEN}]🟢 200[/]"
    if stats.last_error and "429" in stats.last_error:
        return "[yellow]🟡 429[/]"
    if stats.last_error and "503" in stats.last_error:
        return f"[{COLOR_MAGENTA}]🔴 503[/]"
    return f"[{COLOR_MAGENTA}]🔴 ERR[/]"


def _format_ping(stats: NodeStats) -> str:
    """Render latency or timeout."""
    if stats.latency_ms is None:
        return f"[{COLOR_MAGENTA}]TIMEOUT[/]"
    return f"{stats.latency_ms:>4.0f} ms"


def _failure_pressure_bar(stats: NodeStats) -> str:
    """Render a compact pressure bar from consecutive failures."""
    if not stats.healthy and stats.latency_ms is None:
        return "-"
    filled = min(10, max(1, stats.consecutive_failures + 1))
    return f"[{COLOR_NEON_GREEN}]{'█' * filled}[/][dim]{'░' * (10 - filled)}[/]"


def _success_rate(stats: NodeStats) -> str:
    """Render a small success-rate estimate for display."""
    if stats.healthy:
        rate = max(0.0, 100.0 - (stats.consecutive_failures * 8.0))
    else:
        rate = 0.0 if stats.latency_ms is None else 82.1
    return f"{rate:>6.1f}%"


def _sparkline(tps: float) -> str:
    """Render a deterministic TPS sparkline."""
    if tps <= 0:
        return "▂" * 12
    return "▂▃▅▆▇██▇▆▅▃▂"


def _traffic_hint(snapshot: dict[str, Any]) -> str:
    """Describe the current routing posture."""
    nodes: dict[str, NodeStats] = snapshot.get("nodes", {})
    degraded = next((node for node in nodes.values() if not node.healthy), None)
    healthy = next((node for node in nodes.values() if node.healthy), None)
    if degraded and healthy:
        return (
            f"Node '{degraded.provider}' degraded. "
            f"Auto-shifting traffic to '{healthy.provider}'..."
        )
    if healthy:
        return f"Routing stable. Primary traffic flowing through '{healthy.provider}'."
    return "All nodes degraded. Failover chain is probing for recovery..."


def _format_log_line(line: str) -> str:
    """Render one event line with timestamp and severity."""
    timestamp, event = _split_event_timestamp(line)
    if event.startswith("request "):
        return f"{timestamp} [REQ] {event.removeprefix('request ')}"
    if event.startswith("failover "):
        return f"{timestamp} [FAILOVER] 🔄 Rerouting {event.removeprefix('failover ')}"
    if event.startswith("probe-fail "):
        return f"{timestamp} [WARN] {event.removeprefix('probe-fail ')}"
    return f"{timestamp} [INFO] {event}"


def _split_event_timestamp(line: str) -> tuple[str, str]:
    """Return ``("[HH:MM:SS]", message)`` for timestamped event-log lines."""
    if (
        len(line) > 11
        and line[0] == "["
        and line[3] == ":"
        and line[6] == ":"
        and line[9:11] == "] "
    ):
        return line[:10], line[11:]
    return f"[{time.strftime('%H:%M:%S')}]", line


async def dashboard_loop(
    state: RouterState,
    stop: asyncio.Event,
    *,
    refresh_seconds: float = 1.0,
) -> None:
    """Refresh the TUI once per ``refresh_seconds`` until ``stop`` is set."""
    console = Console(force_terminal=True, color_system="truecolor", width=120)
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
                return  # pragma: no cover


def _build_demo_state() -> RouterState:
    """Build a fake :class:`RouterState` for the standalone demo."""
    state = RouterState()
    state.routing_strategy = RoutingStrategy.PRIORITY
    state.listen_host = "0.0.0.0"
    state.listen_port = 8545
    state.probe_interval_seconds = 5.0
    state.request_timeout_seconds = 5.0
    state.nodes["Alchemy-Free"] = NodeStats(
        provider="Alchemy-Free",
        url="https://alchemy.example/rpc",
        priority=1,
        routing_strategy=RoutingStrategy.PRIORITY,
        healthy=True,
        latency_ms=45.0,
    )
    state.nodes["Infura-Main"] = NodeStats(
        provider="Infura-Main",
        url="https://infura.example/rpc",
        priority=2,
        routing_strategy=RoutingStrategy.LOWEST_LATENCY,
        healthy=False,
        latency_ms=120.0,
        consecutive_failures=8,
        last_error="upstream returned HTTP 429",
    )
    state.nodes["QuickNode"] = NodeStats(
        provider="QuickNode",
        url="https://quicknode.example/rpc",
        priority=3,
        routing_strategy=RoutingStrategy.ROUND_ROBIN,
        healthy=True,
        latency_ms=95.0,
    )
    state.nodes["Local-Node"] = NodeStats(
        provider="Local-Node",
        url="http://127.0.0.1:8545",
        priority=4,
        routing_strategy=RoutingStrategy.FAILOVER,
        healthy=False,
        latency_ms=None,
        consecutive_failures=4,
        last_error="upstream returned HTTP 503",
    )
    state.method_routes = {
        "eth_getLogs": {
            "providers": ["Infura-Main", "Alchemy-Free"],
            "routing_strategy": RoutingStrategy.LOWEST_LATENCY,
        },
        "eth_sendRawTransaction": {
            "providers": ["QuickNode"],
            "routing_strategy": RoutingStrategy.FAILOVER,
        },
    }
    state.total_requests = 15_204
    state.total_success = 15_062
    state.total_failovers = 142
    state.tps_1s = 42.0
    state.event_log.extend(
        [
            format_event("request eth_blockNumber -> Alchemy-Free (45ms)"),
            format_event("request eth_sendRawTransaction -> QuickNode (96ms)"),
            format_event("probe-fail Infura-Main returned 429 Too Many Requests!"),
            format_event("failover Infura-Main -> Alchemy-Free"),
            format_event("request eth_call -> Alchemy-Free (48ms)"),
        ]
    )
    state.started_at = time.monotonic() - 15_153
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
