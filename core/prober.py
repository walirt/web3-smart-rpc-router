"""Background health prober for the Web3 Smart RPC Router.

The prober runs once per ``probe_interval_seconds`` and issues a cheap
``eth_blockNumber`` call against every configured :class:`RpcNode`. The
result of each call is folded back into the shared :class:`RouterState`
so the proxy's :func:`core.router.select_node` can pick the lowest-
latency healthy node on the next request.

A single failing node must never bring the loop down: every tick is
wrapped in a broad ``try / except`` that swallows the error, records
it on the corresponding :class:`NodeStats`, and moves on. The loop
also honours an :class:`asyncio.Event` (``stop``) that :func:`main_async`
sets on shutdown so the orchestrator can wind the prober down cleanly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from core.models import RpcNode
from core.state import NodeStats, RouterState, format_event

_LOGGER = logging.getLogger(__name__)


# JSON-RPC envelope for the probe call. ``id`` is fixed at ``0`` — the
# upstream's reply is discarded after the latency timer fires.
_PROBE_PAYLOAD: dict[str, object] = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "eth_blockNumber",
    "params": [],
}


def _record_success(stats: NodeStats, latency_ms: float, when: float) -> None:
    """Update ``stats`` to reflect a successful probe call."""
    stats.latency_ms = latency_ms
    stats.healthy = True
    stats.consecutive_failures = 0
    stats.last_error = None
    stats.last_probed_at = when
    stats.circuit_open_until = None


def _record_failure(state: RouterState, stats: NodeStats, error: str, when: float) -> None:
    """Update ``stats`` to reflect a failed probe call."""
    state.record_node_failure(stats.provider, error, when=when)


async def probe_once(
    state: RouterState,
    cfg: list[RpcNode],
    client: aiohttp.ClientSession,
    *,
    request_timeout_seconds: float = 5.0,
) -> None:
    """Run a single probe pass over every node in ``cfg``.

    Failures are recorded on the corresponding :class:`NodeStats` and a
    ``"probe-fail <provider> <error>"`` line is appended to the event
    tape. The function never raises.
    """
    timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
    for node in cfg:
        stats = state.nodes[node.provider]
        when = time.monotonic()
        start = time.perf_counter()
        try:
            async with client.post(
                node.url,
                json=_PROBE_PAYLOAD,
                headers=node.headers,
                timeout=timeout,
            ) as resp:
                if resp.status >= 400:
                    err = f"upstream returned HTTP {resp.status}"
                    async with state.transaction():
                        _record_failure(state, stats, err, when)
                        state.event_log.append(
                            format_event(f"probe-fail {node.provider} {err}")
                        )
                    continue
                # Drain the body so the connection can be released.
                await resp.read()
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
            aiohttp.ContentTypeError,
        ) as exc:
            err = f"{type(exc).__name__}: {exc}"
            async with state.transaction():
                _record_failure(state, stats, err, when)
                state.event_log.append(
                    format_event(f"probe-fail {node.provider} {err}")
                )
            continue
        latency_ms = (time.perf_counter() - start) * 1000.0
        async with state.transaction():
            _record_success(stats, latency_ms, when)


async def prober_loop(
    state: RouterState,
    cfg: list[RpcNode],
    client: aiohttp.ClientSession,
    stop: asyncio.Event,
    *,
    probe_interval_seconds: float = 5.0,
) -> None:
    """Probe every node at a fixed cadence until ``stop`` is set.

    Each tick is wrapped in a broad ``try / except`` so a single
    failure cannot take the loop down. The interval is honoured
    cooperatively via :func:`asyncio.wait_for` against the ``stop``
    event, so the loop wakes immediately on shutdown rather than
    waiting out the full interval.
    """
    while not stop.is_set():
        try:
            await probe_once(state, cfg, client)
        except asyncio.CancelledError:
            raise  # pragma: no cover - cancellation propagation
        except BaseException as exc:  # noqa: BLE001 - tick isolation
            _LOGGER.warning("prober tick failed: %r", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=probe_interval_seconds)
        except asyncio.TimeoutError:
            continue
        else:
            return


__all__ = ["probe_once", "prober_loop"]
