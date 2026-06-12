"""End-to-end integration tests for the Web3 Smart RPC Router.

These tests boot the full :func:`core.router.main_async` orchestrator
in-process against a temp config file, mock both upstream endpoints
with :mod:`aioresponses`, and fire a real ``POST /`` through the
in-process aiohttp server. They are the canonical verification of
AC-11 (the full proxy stack works end-to-end) and AC-6 (the
``python -m core.router`` entry point wires the proxy, the prober,
and the TUI together).
"""
from __future__ import annotations

import asyncio
import runpy
import socket
import sys
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import yaml
from aioresponses import aioresponses

from core.router import _parse_args, main, main_async


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return a TCP port the OS has not yet bound (best effort)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def integration_config(tmp_path: Path) -> Path:
    """Write a small, valid config that points at two mocked upstreams."""
    port = _free_port()
    config = {
        "global": {
            "listen_port": port,
            "probe_interval_seconds": 0.05,
            "request_timeout_seconds": 2.0,
            "max_retries": 3,
        },
        "rpc_nodes": [
            {
                "provider": "alpha",
                "url": "https://alpha.test/rpc",
                "routing_strategy": "priority",
                "priority": 1,
                "weight": 1,
                "headers": {},
            },
            {
                "provider": "beta",
                "url": "https://beta.test/rpc",
                "routing_strategy": "failover",
                "priority": 2,
                "weight": 1,
                "headers": {},
            },
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC-11: full app in-process, real HTTP through the proxy
# ---------------------------------------------------------------------------


async def test_main_async_proxies_post_through_to_upstream(
    integration_config: Path,
) -> None:
    """``main_async`` starts the proxy, the prober, and accepts a real ``POST /``."""
    cfg = _read_yaml(integration_config)
    upstream_body = {"jsonrpc": "2.0", "id": 1, "result": "0x42"}
    # The orchestrator is started in a task so the test can cancel it
    # when it has finished verifying the response.
    runner_task = asyncio.create_task(
        main_async(str(integration_config), with_tui=False)
    )
    # Give the server a moment to bind, then drive a real request.
    await asyncio.sleep(0.4)
    try:
        # Passthrough 127.0.0.1 so the real HTTP request to the local
        # server reaches the in-process app, while upstream mocks are
        # still honoured.
        with aioresponses(passthrough=["http://127.0.0.1"]) as mocked:
            mocked.post("https://alpha.test/rpc", payload=upstream_body, status=200)
            mocked.post("https://beta.test/rpc", payload=upstream_body, status=200)
            async with aiohttp.ClientSession() as client:
                resp = await client.post(
                    f"http://127.0.0.1:{cfg['global']['listen_port']}/",
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber"},
                )
                assert resp.status == 200
                body = await resp.json()
                assert body == upstream_body
    finally:
        runner_task.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, BaseException):
            pass


async def test_main_async_healthz_endpoint(integration_config: Path) -> None:
    """``GET /healthz`` on the running server returns ``{"ok": true}``."""
    cfg = _read_yaml(integration_config)
    runner_task = asyncio.create_task(
        main_async(str(integration_config), with_tui=False)
    )
    await asyncio.sleep(0.4)
    try:
        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"http://127.0.0.1:{cfg['global']['listen_port']}/healthz"
            )
            assert resp.status == 200
            body = await resp.json()
            assert body == {"ok": True}
    finally:
        runner_task.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, BaseException):
            pass


# ---------------------------------------------------------------------------
# AC-6: __main__ entry point
# ---------------------------------------------------------------------------


def test_main_module_runs_with_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``python -m core.router --help`` prints usage and exits 0."""
    # ``main(["--help"])`` invokes argparse which calls ``SystemExit(0)``.
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "--with-tui" in out


def test_main_module_runs_via_runpy() -> None:
    """``runpy.run_module("core.router", run_name="__main__")`` honours --help."""
    old_argv = sys.argv
    sys.argv = ["core.router", "--help"]
    try:
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("core.router", run_name="__main__")
        assert excinfo.value.code == 0
    finally:
        sys.argv = old_argv


def test_parse_args_default() -> None:
    """``_parse_args`` reads the positional config and leaves --with-tui off by default."""
    args = _parse_args(["/tmp/config.yaml"])
    assert args.config == "/tmp/config.yaml"
    assert args.with_tui is False


def test_parse_args_with_tui() -> None:
    """``--with-tui`` flips the boolean flag."""
    args = _parse_args(["/tmp/config.yaml", "--with-tui"])
    assert args.with_tui is True


# ---------------------------------------------------------------------------
# main_async with the TUI attached
# ---------------------------------------------------------------------------


async def test_main_async_with_tui_runs_dashboard(
    integration_config: Path,
) -> None:
    """``main_async(..., with_tui=True)`` schedules the dashboard task without crashing."""
    runner_task = asyncio.create_task(
        main_async(str(integration_config), with_tui=True)
    )
    # Let the prober tick and the TUI render at least one frame.
    await asyncio.sleep(0.4)
    try:
        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"http://127.0.0.1:{_read_yaml(integration_config)['global']['listen_port']}/healthz"
            )
            assert resp.status == 200
    finally:
        runner_task.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, BaseException):
            pass


# ---------------------------------------------------------------------------
# main() KeyboardInterrupt path
# ---------------------------------------------------------------------------


def test_main_keyboard_interrupt_prints_shutdown(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Ctrl+C`` is caught and prints the shutdown line on stderr."""
    async def raise_keyboard_interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("core.router.main_async", raise_keyboard_interrupt)
    main(["/tmp/config.yaml"])  # must not raise
    err = capsys.readouterr().err
    assert "shutting down" in err.lower()


# ---------------------------------------------------------------------------
# main_async with the TUI attached
# ---------------------------------------------------------------------------


async def test_main_async_with_tui_runs_dashboard(
    integration_config: Path,
) -> None:
    """``main_async(..., with_tui=True)`` schedules the dashboard task without crashing."""
    runner_task = asyncio.create_task(
        main_async(str(integration_config), with_tui=True)
    )
    # Let the prober tick and the TUI render at least one frame.
    await asyncio.sleep(0.4)
    try:
        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"http://127.0.0.1:{_read_yaml(integration_config)['global']['listen_port']}/healthz"
            )
            assert resp.status == 200
    finally:
        runner_task.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, BaseException):
            pass


# ---------------------------------------------------------------------------
# main() KeyboardInterrupt path
# ---------------------------------------------------------------------------


