"""aiohttp proxy and routing engine for the Web3 Smart RPC Router.

The :class:`ProxyHandler` accepts JSON-RPC requests on ``POST /`` and
forwards them to one of the configured upstream nodes. A request is
forwarded to the node picked by :func:`select_node`; if that call
returns a non-2xx status (``429`` or any ``5xx``), the underlying
client raises, or the response body is not a JSON object, the router
moves on to the next node in priority order, applying a bounded
exponential backoff between attempts. The caller is never exposed to
the upstream's transient errors; the router always either returns a
2xx JSON body or a ``503 no healthy upstream`` response.

A ``GET /healthz`` endpoint is also exposed for liveness probes and
returns a static ``{"ok": true}`` body.

The :func:`main_async` coroutine is the canonical entry point used by
``python -m core.router`` — it wires the proxy, the background health
prober, and the optional TUI together on a single asyncio event loop.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any, Optional

import aiohttp
from aiohttp import web

from core.config import load_config
from core.models import RpcNode, RoutingStrategy
from core.state import RouterState

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NoHealthyNodeError(Exception):
    """Raised when :func:`select_node` cannot find a usable node, or
    :func:`forward_with_failover` exhausts every configured node."""


# ---------------------------------------------------------------------------
# Node selection
# ---------------------------------------------------------------------------


def _healthy_pool(state: RouterState, cfg: list[RpcNode]) -> list[RpcNode]:
    """Return the subset of ``cfg`` whose :class:`NodeStats` is healthy."""
    return [n for n in cfg if state.nodes[n.provider].healthy]


def _self_healing_pool(state: RouterState, cfg: list[RpcNode]) -> list[RpcNode]:
    """Healthy subset, falling back to the full ``cfg`` when every node is sick.

    Used by :func:`forward_with_failover` so the proxy can keep trying
    upstreams after a global outage clears; :func:`select_node` does
    **not** use this fallback because a router with no healthy node has
    no meaningful node to hand back to a caller.
    """
    healthy = _healthy_pool(state, cfg)
    return healthy if healthy else list(cfg)


def select_node(state: RouterState, cfg: list[RpcNode]) -> RpcNode:
    """Pick one upstream node according to the active :class:`RoutingStrategy`.

    The active strategy is the one declared on the first node in
    ``cfg``; mixing strategies across nodes in a single config is
    permitted by the schema but the router only ever honours one. When
    the active strategy is :attr:`RoutingStrategy.ROUND_ROBIN` the
    :attr:`RouterState.round_robin_index` is incremented synchronously;
    asyncio is single-threaded so a synchronous mutation here is
    race-free with respect to other coroutines.

    Raises :class:`NoHealthyNodeError` when the configured node set is
    empty, or when every node is currently marked unhealthy. (The
    self-heal fallback that retries every node after a global outage
    lives in :func:`forward_with_failover`, not here.)
    """
    if not cfg:
        raise NoHealthyNodeError("no nodes configured")
    strategy = cfg[0].routing_strategy
    healthy = _healthy_pool(state, cfg)
    if not healthy:
        raise NoHealthyNodeError("no healthy nodes available")
    if strategy is RoutingStrategy.PRIORITY:
        return min(healthy, key=lambda n: n.priority)
    if strategy is RoutingStrategy.FAILOVER:
        return min(healthy, key=lambda n: n.priority)
    if strategy is RoutingStrategy.ROUND_ROBIN:
        ordered = sorted(healthy, key=lambda n: n.priority)
        chosen = ordered[state.round_robin_index % len(ordered)]
        state.round_robin_index += 1
        return chosen
    if strategy is RoutingStrategy.LOWEST_LATENCY:
        with_latency = [
            n for n in healthy if state.nodes[n.provider].latency_ms is not None
        ]
        if with_latency:
            return min(
                with_latency,
                key=lambda n: state.nodes[n.provider].latency_ms,  # type: ignore[arg-type,return-value]
            )
        # No latency data yet — fall back to priority order.
        return min(healthy, key=lambda n: n.priority)
    raise NoHealthyNodeError(f"unknown routing strategy: {strategy!r}")


# ---------------------------------------------------------------------------
# Forwarding with bounded failover + exponential backoff
# ---------------------------------------------------------------------------


def _is_transient_status(status: int) -> bool:
    """``True`` for statuses that should trigger a failover hop."""
    return status == 429 or 500 <= status < 600


def _backoff_delay(
    attempt_index: int, *, base: float, cap: float
) -> float:
    """Return the sleep duration before attempt ``attempt_index + 1``."""
    if attempt_index <= 0:
        return 0.0
    return float(min(base * (2 ** (attempt_index - 1)), cap))


async def forward_with_failover(
    state: RouterState,
    cfg: list[RpcNode],
    client: aiohttp.ClientSession,
    payload: dict[str, Any],
    *,
    request_timeout_seconds: float,
) -> tuple[dict[str, Any], str]:
    """Forward ``payload`` to a healthy upstream, retrying on transient failures.

    Returns the parsed JSON body (a ``dict``) and the provider label of
    the node that answered. Raises :class:`NoHealthyNodeError` if every
    configured node returns a transient failure.

    The backoff schedule between attempts is bounded exponential with
    ``base = request_timeout_seconds / 4`` and
    ``cap = request_timeout_seconds * 4``.
    """
    base = request_timeout_seconds / 4
    cap = request_timeout_seconds * 4
    pool = _self_healing_pool(state, cfg)
    ordered = sorted(pool, key=lambda n: n.priority)
    if not ordered:
        await state.record_request(success=False)
        raise NoHealthyNodeError("no nodes configured")
    timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
    last_exc: Optional[BaseException] = None
    for i, node in enumerate(ordered):
        if i > 0:
            await asyncio.sleep(_backoff_delay(i, base=base, cap=cap))
            await state.record_failover(ordered[i - 1].provider, node.provider)
        try:
            async with client.post(
                node.url,
                json=payload,
                headers=node.headers,
                timeout=timeout,
            ) as resp:
                if _is_transient_status(resp.status):
                    continue
                body = await resp.json(content_type=None)
                if not isinstance(body, dict):
                    # Non-mapping body (e.g. JSON array or scalar): treat as failure.
                    continue
                await state.record_request(success=True)
                return body, node.provider
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
            aiohttp.ContentTypeError,
        ) as exc:
            last_exc = exc
            continue
    await state.record_request(success=False)
    detail = f"last error: {last_exc!r}" if last_exc is not None else "no attempts made"
    raise NoHealthyNodeError(
        f"all {len(ordered)} upstream node(s) failed; {detail}"
    )


# ---------------------------------------------------------------------------
# aiohttp glue
# ---------------------------------------------------------------------------


async def _healthz(_request: web.Request) -> web.Response:
    """Liveness probe — always returns ``{"ok": true}``."""
    return web.json_response({"ok": True})


class ProxyHandler:
    """aiohttp handler that proxies JSON-RPC requests to a healthy upstream.

    The handler is intentionally small: it parses the request body,
    delegates the actual forwarding to :func:`forward_with_failover`,
    and translates the various outcomes (success, no-healthy-upstream,
    invalid payload) into HTTP responses. The aiohttp
    :class:`~aiohttp.ClientSession` and the upstream request timeout
    are stashed on the :class:`~aiohttp.web.Application` at
    construction time so this class can stay a thin adapter.
    """

    def __init__(
        self,
        state: RouterState,
        cfg: list[RpcNode],
        *,
        request_timeout_seconds: float,
    ) -> None:
        self.state = state
        self.cfg = cfg
        self.request_timeout_seconds = request_timeout_seconds

    async def handle(self, request: web.Request) -> web.Response:
        """Handle a single ``POST /`` request and return a :class:`web.Response`."""
        try:
            raw = await request.json()
        except (json.JSONDecodeError, aiohttp.ContentTypeError):
            return web.json_response({"error": "invalid jsonrpc"}, status=400)
        if not isinstance(raw, dict):
            return web.json_response({"error": "invalid jsonrpc"}, status=400)
        client: aiohttp.ClientSession = request.app["upstream_client"]
        try:
            body, _provider = await forward_with_failover(
                self.state,
                self.cfg,
                client,
                raw,
                request_timeout_seconds=self.request_timeout_seconds,
            )
        except NoHealthyNodeError:
            return web.json_response(
                {"error": "no healthy upstream"}, status=503
            )
        return web.json_response(body, status=200)


def make_app(
    state: RouterState,
    cfg: list[RpcNode],
    *,
    upstream_client: Optional[aiohttp.ClientSession] = None,
    request_timeout_seconds: float = 10.0,
) -> web.Application:
    """Build an :class:`aiohttp.web.Application` exposing ``POST /`` and ``GET /healthz``.

    ``upstream_client`` is the session used to call upstream RPC nodes
    and is stashed on the application so the handler can reach it
    without a global. ``request_timeout_seconds`` is the per-request
    timeout passed to :func:`forward_with_failover` for both the
    upstream call and the exponential-backoff schedule.
    """
    app = web.Application()
    app["state"] = state
    app["cfg"] = cfg
    app["upstream_client"] = upstream_client
    app["request_timeout_seconds"] = request_timeout_seconds
    handler = ProxyHandler(
        state, cfg, request_timeout_seconds=request_timeout_seconds
    )
    app.router.add_post("/", handler.handle)
    app.router.add_get("/healthz", _healthz)
    return app


# ---------------------------------------------------------------------------
# main_async: tie the proxy, the prober, and the optional TUI together.
# ---------------------------------------------------------------------------


async def main_async(cfg_path: str, with_tui: bool = False) -> None:
    """Boot the full router and run until :class:`KeyboardInterrupt` cancels it.

    The orchestrator starts the proxy, the background health prober,
    and (when ``with_tui`` is set) the Rich TUI dashboard as separate
    ``asyncio.Task`` instances on the same event loop. A single
    :class:`asyncio.Event` is awaited on until a ``KeyboardInterrupt``
    (or an ``SIGTERM``) cancels it; the cleanup phase cancels every
    task and closes the upstream :class:`~aiohttp.ClientSession`.
    """
    # Imported lazily so the module is usable in tests without Rich /
    # the prober as runtime dependencies of the basic proxy logic.
    from core.prober import prober_loop

    cfg = load_config(cfg_path)
    state = RouterState.from_config(cfg.rpc_nodes)
    stop = asyncio.Event()

    async def _wait_for_signal() -> None:
        loop = asyncio.get_running_loop()
        # ``add_signal_handler`` is unavailable on Windows; on POSIX it
        # gives us a much cleaner shutdown than asyncio's default.
        if os.name != "posix":
            try:
                await asyncio.Future()  # never completes; relies on KeyboardInterrupt
            finally:
                stop.set()
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover - safety net
                pass
        await stop.wait()

    timeout = aiohttp.ClientTimeout(total=cfg.global_.request_timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        prober_task = asyncio.create_task(
            prober_loop(
                state, cfg.rpc_nodes, client, stop,
                probe_interval_seconds=cfg.global_.probe_interval_seconds,
            ),
            name="prober-loop",
        )
        # Re-bind the upstream client on the app so the handler can
        # reach it.
        app = make_app(
            state,
            cfg.rpc_nodes,
            upstream_client=client,
            request_timeout_seconds=cfg.global_.request_timeout_seconds,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=cfg.global_.listen_port)
        await site.start()
        _LOGGER.info(
            "router listening on http://127.0.0.1:%s (tui=%s)",
            cfg.global_.listen_port,
            with_tui,
        )
        tasks: list[asyncio.Task[None]] = [prober_task]
        if with_tui:
            # Imported lazily for the same reason as the prober.
            from ui.dashboard import dashboard_loop

            dashboard_task = asyncio.create_task(
                dashboard_loop(state, stop), name="dashboard-loop"
            )
            tasks.append(dashboard_task)
        try:
            await _wait_for_signal()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                    pass
            await runner.cleanup()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="core.router",
        description=(
            "Run the Web3 Smart RPC Router: a transparent aiohttp "
            "JSON-RPC proxy with failover and a health prober."
        ),
    )
    parser.add_argument(
        "config",
        help="Path to a YAML router config file (see core/models.py).",
    )
    parser.add_argument(
        "--with-tui",
        action="store_true",
        help="Also launch the cyberpunk Rich TUI dashboard in this process.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Synchronous entry point used by ``python -m core.router``."""
    args = _parse_args(argv)
    try:
        asyncio.run(main_async(args.config, with_tui=args.with_tui))
    except KeyboardInterrupt:
        # asyncio.run already cleans up the loop and tasks on cancel;
        # this branch just makes the shutdown legible in the terminal.
        print("\nrouter: shutting down", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - exercised via __main__ test
    main()


__all__ = [
    "NoHealthyNodeError",
    "ProxyHandler",
    "forward_with_failover",
    "main",
    "main_async",
    "make_app",
    "select_node",
]
