"""In-memory state model for the Web3 Smart RPC Router.

The router holds exactly one :class:`RouterState` instance per process.
The instance is mutated by three classes of writers:

* The :class:`~core.router.ProxyHandler` — increments per-request
  counters and appends ``failover`` lines to the event tape.
* The :class:`~core.prober.prober_loop` — refreshes each node's
  :class:`NodeStats` once per tick.
* The :func:`~core.router.select_node` helper — bumps the
  ``round_robin_index`` counter when the active strategy is
  :attr:`~core.models.RoutingStrategy.ROUND_ROBIN`.

All three writers must serialise their reads and writes through the
:class:`RouterState` instance's :attr:`lock`; the
``transaction()`` async context manager is the only supported way to
do that. The TUI is a *reader* and uses :func:`snapshot` to obtain a
``copy.deepcopy`` decoupled from live mutations — it must never
acquire the lock and must never mutate the state.
"""
from __future__ import annotations

import asyncio
import copy
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable, Optional

from core.models import MethodRoute, RpcNode, RoutingStrategy


# Cap on the in-memory event-tape length. Older lines are silently
# dropped once this is reached.
_EVENT_LOG_CAPACITY: int = 256

# Window used by the rolling ``tps_1s`` calculation.
_TPS_WINDOW_SECONDS: float = 1.0

# Circuit breaker defaults. They are runtime constants rather than YAML fields
# so the public config contract stays small while the router gains protection
# against repeatedly hammering a degraded public endpoint.
CIRCUIT_FAILURE_THRESHOLD: int = 3
CIRCUIT_COOLDOWN_SECONDS: float = 30.0


def format_event(message: str, *, timestamp: float | None = None) -> str:
    """Prefix an event message with the wall-clock time when it was recorded."""
    when = time.localtime(time.time() if timestamp is None else timestamp)
    return f"[{time.strftime('%H:%M:%S', when)}] {message}"


@dataclass
class NodeStats:
    """Mutable, per-node runtime statistics.

    The first four fields are a static copy of the corresponding
    :class:`~core.models.RpcNode` configuration values; the rest are
    refreshed by the background prober.
    """

    provider: str
    url: str
    priority: int
    routing_strategy: RoutingStrategy
    healthy: bool = True
    latency_ms: Optional[float] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_probed_at: Optional[float] = None
    circuit_open_until: Optional[float] = None

    def is_circuit_open(self, now: float | None = None) -> bool:
        """Return ``True`` while this node is inside its cooldown window."""
        if self.circuit_open_until is None:
            return False
        return self.circuit_open_until > (time.monotonic() if now is None else now)


@dataclass
class RouterState:
    """Single-process, in-memory router state.

    The :func:`from_config` classmethod seeds ``nodes`` from a list of
    :class:`~core.models.RpcNode` instances. The :func:`snapshot` method
    returns a ``copy.deepcopy`` decoupled from live mutations; the TUI
    consumes snapshots and never touches the live instance directly.
    """

    nodes: dict[str, NodeStats] = field(default_factory=dict)
    method_routes: dict[str, dict[str, object]] = field(default_factory=dict)
    routing_strategy: RoutingStrategy = RoutingStrategy.PRIORITY
    listen_host: str = "127.0.0.1"
    listen_port: int | None = None
    probe_interval_seconds: float | None = None
    request_timeout_seconds: float | None = None
    round_robin_index: int = 0
    total_requests: int = 0
    total_success: int = 0
    total_failovers: int = 0
    tps_1s: float = 0.0
    event_log: "deque[str]" = field(
        default_factory=lambda: deque(maxlen=_EVENT_LOG_CAPACITY)
    )
    # Bookkeeping for the rolling TPS window. Stored on the instance so
    # ``snapshot`` can include it if ever needed; not part of the
    # public dataclass surface (prefixed with ``_``).
    _request_timestamps: "deque[float]" = field(
        default_factory=deque,
        repr=False,
        compare=False,
    )
    started_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        # ``asyncio.Lock`` cannot be a default factory value because it
        # binds to a running loop on creation; build it lazily in
        # ``__post_init__`` so the dataclass remains picklable and
        # easier to reason about in tests.
        self.lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        rpc_nodes: Iterable[RpcNode],
        routing_strategy: RoutingStrategy = RoutingStrategy.PRIORITY,
        method_routes: dict[str, MethodRoute] | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int | None = None,
        probe_interval_seconds: float | None = None,
        request_timeout_seconds: float | None = None,
    ) -> "RouterState":
        """Build a fresh :class:`RouterState` seeded from ``rpc_nodes``."""
        state = cls()
        state.routing_strategy = routing_strategy
        state.listen_host = listen_host
        state.listen_port = listen_port
        state.probe_interval_seconds = probe_interval_seconds
        state.request_timeout_seconds = request_timeout_seconds
        for node in rpc_nodes:
            state.nodes[node.provider] = NodeStats(
                provider=node.provider,
                url=node.url,
                priority=node.priority,
                routing_strategy=routing_strategy,
            )
        if method_routes:
            state.method_routes = {
                method: {
                    "providers": list(route.providers),
                    "routing_strategy": route.routing_strategy or routing_strategy,
                }
                for method, route in method_routes.items()
            }
        return state

    # ------------------------------------------------------------------
    # Mutation helpers (must be called from inside ``transaction()``)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["RouterState"]:
        """Acquire :attr:`lock` and yield ``self`` for a single critical section.

        The lock is always released, even when the inner block raises.
        Nested calls on the same :class:`RouterState` instance are
        allowed: ``asyncio.Lock`` is non-reentrant, but the canonical
        use of this manager is to wrap a single logical mutation.
        """
        async with self.lock:
            yield self

    async def record_event(self, message: str) -> None:
        """Append ``message`` to :attr:`event_log` under the lock.

        The deque is bounded at :data:`_EVENT_LOG_CAPACITY`; the oldest
        entries are silently dropped when the cap is reached.
        """
        async with self.transaction():
            self.event_log.append(format_event(message))

    async def record_failover(self, from_provider: str, to_provider: str) -> None:
        """Record one failover hop.

        Bumps :attr:`total_failovers` and appends the canonical
        ``"failover <from> -> <to>"`` line to the event tape.
        """
        async with self.transaction():
            self.total_failovers += 1
            self.event_log.append(format_event(f"failover {from_provider} -> {to_provider}"))

    def record_node_success(self, provider: str, *, latency_ms: float | None = None) -> None:
        """Mark ``provider`` healthy and close any open circuit.

        This method expects the caller to hold :meth:`transaction` when
        coordinating with other state mutations.
        """
        stats = self.nodes[provider]
        if latency_ms is not None:
            stats.latency_ms = latency_ms
        stats.healthy = True
        stats.consecutive_failures = 0
        stats.last_error = None
        stats.circuit_open_until = None

    def record_node_failure(
        self,
        provider: str,
        error: str,
        *,
        when: float | None = None,
    ) -> None:
        """Mark ``provider`` degraded and open its circuit after repeated failures.

        This method expects the caller to hold :meth:`transaction` when
        coordinating with other state mutations.
        """
        now = time.monotonic() if when is None else when
        stats = self.nodes[provider]
        stats.latency_ms = None
        stats.healthy = False
        stats.consecutive_failures += 1
        stats.last_error = error
        stats.last_probed_at = now
        if stats.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            stats.circuit_open_until = now + CIRCUIT_COOLDOWN_SECONDS

    async def record_request(self, success: bool) -> None:
        """Update per-request counters and refresh :attr:`tps_1s`.

        A successful request increments :attr:`total_success`; a failed
        one (no healthy node could be reached) increments
        :attr:`total_failovers` instead. Every call increments
        :attr:`total_requests`. The rolling TPS window holds the
        monotonic timestamps of the last second's worth of requests.
        """
        now = time.monotonic()
        async with self.transaction():
            self.total_requests += 1
            if success:
                self.total_success += 1
            else:
                self.total_failovers += 1
            self._request_timestamps.append(now)
            cutoff = now - _TPS_WINDOW_SECONDS
            while self._request_timestamps and self._request_timestamps[0] < cutoff:
                self._request_timestamps.popleft()
            self.tps_1s = float(len(self._request_timestamps))

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Return a ``copy.deepcopy`` of the state decoupled from live mutations.

        The returned ``dict`` is a stand-in for an immutable record type:
        tests and the TUI can mutate it freely without affecting the
        live state. The internal ``_request_timestamps`` deque is
        intentionally excluded from the snapshot because it is an
        implementation detail of the rolling TPS window.
        """
        return {
            "nodes": copy.deepcopy(self.nodes),
            "method_routes": copy.deepcopy(self.method_routes),
            "routing_strategy": self.routing_strategy,
            "listen_host": self.listen_host,
            "listen_port": self.listen_port,
            "probe_interval_seconds": self.probe_interval_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "round_robin_index": self.round_robin_index,
            "total_requests": self.total_requests,
            "total_success": self.total_success,
            "total_failovers": self.total_failovers,
            "tps_1s": self.tps_1s,
            "event_log": list(self.event_log),
            "started_at": self.started_at,
        }


__all__ = [
    "CIRCUIT_COOLDOWN_SECONDS",
    "CIRCUIT_FAILURE_THRESHOLD",
    "NodeStats",
    "RouterState",
    "format_event",
]
