# CyOps Build Plan 2 - Runtime Router And TUI

## Goal

Deliver the local Web3 Smart RPC Router runtime: an aiohttp JSON-RPC proxy with
transparent failover, health probing, in-memory state, and a read-only Rich TUI.

## Scope

Included:

- Local `POST /` JSON-RPC forwarding endpoint.
- `GET /healthz` liveness endpoint.
- Failover on `429`, `5xx`, network errors, timeouts, and invalid upstream JSON.
- Routing strategies: `priority`, `round_robin`, `lowest_latency`, `failover`.
- Background health probes using `eth_blockNumber`.
- In-memory runtime state with lock-protected writes and deep-copy snapshots.
- Rich terminal dashboard launched with `--with-tui`.
- Integration tests with mocked upstream RPC providers.

Out of scope:

- Public deployment.
- WebSocket upstreams and `eth_subscribe`.
- Browser UI.
- Persistent metrics storage.
- Authentication and per-client rate limits.

## Acceptance Criteria

| ID | Requirement |
|---|---|
| AC-1 | `core/state.py` defines `NodeStats` and `RouterState` with counters, event log, lock, and snapshot support. |
| AC-2 | `core/router.py` exposes selection, failover, app wiring, and `main_async()`. |
| AC-3 | `forward_with_failover()` retries transient failures and hides intermediate upstream errors from clients. |
| AC-4 | Backoff is bounded and derived from `request_timeout_seconds`. |
| AC-5 | `core/prober.py` probes every configured node and isolates per-tick failures. |
| AC-6 | `ui/dashboard.py` renders a read-only TUI from `RouterState.snapshot()`. |
| AC-7 | The app can run with `python -m core.router config.yaml --with-tui`. |
| AC-8 | Tests cover routing strategies, failover, bad upstream bodies, app lifecycle, prober behavior, dashboard rendering, and integration flow. |
| AC-9 | `ruff check core ui tests`, `mypy --strict core ui`, and coverage-gated pytest pass. |
| AC-10 | README documents runtime usage, failover behavior, dashboard panels, and scope boundaries. |

## Implementation Steps

1. Add aiohttp, Rich, pytest-aiohttp, and aioresponses dependencies.
2. Implement `RouterState`, `NodeStats`, transaction helper, bounded event log, and snapshots.
3. Implement node selection and failover in `core/router.py`.
4. Add aiohttp `POST /` and `GET /healthz` handlers.
5. Implement `main_async()` for router/prober/TUI orchestration.
6. Implement `probe_once()` and `prober_loop()`.
7. Implement the Rich dashboard as a read-only observer.
8. Add unit tests per module and an in-process integration test.
9. Update README and verify all quality gates.

## Verification Matrix

| Area | Command or Check | Success Signal |
|---|---|---|
| Router behavior | `pytest -q tests/test_router.py` | All routing and failover cases pass |
| Prober behavior | `pytest -q tests/test_prober.py` | Probe success/failure paths pass |
| Dashboard | `pytest -q tests/test_dashboard.py` | Layout and log formatting pass |
| Integration | `pytest -q tests/test_integration.py` | Local socket request passes through proxy |
| Full quality gate | `pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100` | 100% coverage gate passes |
| Lint and types | `ruff check core ui tests` and `mypy --strict core ui` | No diagnostics |

## Delivered Artifacts

- `core/state.py`
- `core/router.py`
- `core/prober.py`
- `ui/dashboard.py`
- `tests/test_state.py`
- `tests/test_router.py`
- `tests/test_prober.py`
- `tests/test_dashboard.py`
- `tests/test_integration.py`
- Runtime README sections
