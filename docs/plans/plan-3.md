# CyOps Build Plan 3 - Routing Polish And Final Hardening

## Goal

Finish the hackathon-ready version of Web3 Smart RPC Router by adding
method-aware routing, dashboard polish, circuit-breaker protection, bilingual
documentation, and release-ready project notes.

## Scope

Included:

- Global routing strategy cleanup.
- Method-specific provider routing.
- `0.0.0.0` listen host support.
- TUI layout refinements for header, node health, method routing, traffic, and
  live request logs.
- Request routing log events.
- Circuit breaker cooldown for repeatedly failing providers.
- README and Chinese README synchronization.
- CyOps-assisted development notes and technical innovation notes.

Out of scope:

- Runtime LLM calls.
- WebSocket upstream support.
- Weighted routing until it is implemented.
- Retry-budget configuration until it is enforced.
- Production deployment automation.

## Acceptance Criteria

| ID | Requirement |
|---|---|
| AC-1 | `global.routing_strategy` is the default strategy and method routes may override it. |
| AC-2 | `method_routes` validates provider references and supports method-specific provider subsets. |
| AC-3 | The router accepts `listen_host: 0.0.0.0`. |
| AC-4 | TUI header shows router name, probe interval, request timeout, strategy, bind address, status, and uptime. |
| AC-5 | Node Health shows provider, status, ping, pressure, and success rate. |
| AC-6 | Method Routing shows per-method providers and strategy overrides. |
| AC-7 | Live Request Routing prioritizes user request events and preserves probe/failover context. |
| AC-8 | Request events record method, provider, and latency. |
| AC-9 | Circuit breaker opens after repeated provider failures and closes after recovery. |
| AC-10 | README, README_zh, `docs/ai-usage.md`, and `docs/technical-innovation.md` describe the final product accurately. |
| AC-11 | `ruff`, `mypy --strict`, and coverage-gated pytest pass with 100% line and branch coverage for `core` and `ui`. |

## Implementation Steps

1. Move routing strategy from per-node display to global runtime state.
2. Add `method_routes` schema and routing logic.
3. Add a Method Routing TUI panel.
4. Add listen-host support for `0.0.0.0`.
5. Refine the TUI header into a compact 3x3 layout.
6. Rename quota-style UI wording to failure pressure.
7. Record live user request routing events.
8. Replace self-healing-only log wording with live request routing wording.
9. Add circuit breaker state and route selection filtering.
10. Update README and README_zh with final usage, scope, and verification status.
11. Add CyOps-assisted development notes and technical innovation notes.
12. Run all quality gates and review the final diff.

## Verification Matrix

| Area | Command or Check | Success Signal |
|---|---|---|
| Config and schema | `pytest -q tests/test_models.py tests/test_config.py` | Method routes and host validation pass |
| Router | `pytest -q tests/test_router.py` | Method routing, failover, request logs, and circuit breaker pass |
| State and prober | `pytest -q tests/test_state.py tests/test_prober.py` | Snapshot, cooldown, and recovery behavior pass |
| Dashboard | `pytest -q tests/test_dashboard.py` | Header, panels, logs, and cooldown status pass |
| Integration | `pytest -q tests/test_integration.py` | In-process app startup and local request pass |
| Full gate | `pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100` | 100% coverage gate passes |
| Lint and types | `ruff check core ui tests` and `mypy --strict core ui` | No diagnostics |

## Delivered Artifacts

- Method routing in `core/models.py`, `core/router.py`, and `config.yaml`
- Circuit breaker state in `core/state.py`
- Router/prober integration with provider cooldown and recovery
- Refined Rich TUI in `ui/dashboard.py`
- Expanded tests for routing, dashboard, prober, and state
- `README.md`
- `README_zh.md`
- `docs/ai-usage.md`
- `docs/technical-innovation.md`

## Final Notes

The final product remains intentionally local and deterministic. CyOps was used
as the assisted development workflow, while the runtime router avoids LLM calls
so JSON-RPC forwarding remains predictable, lightweight, and easy to debug.
