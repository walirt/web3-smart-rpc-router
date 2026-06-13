# Project Standards - web3-smart-rpc-router

This file defines the engineering conventions that Claude Code and other coding
agents should follow when working in this repository.

## Project Summary

Web3 Smart RPC Router is a local JSON-RPC gateway for Ethereum-style RPC traffic.
It exposes one stable local endpoint, fronts multiple public upstream RPC
providers, and hides transient provider failures from clients through health
checks, failover, and method-aware routing.

The project is built for users who rely on free public RPC endpoints but do not
want every `429`, `5xx`, timeout, or provider outage to break their local tools,
wallet flows, or scripts. It is intentionally lighter than running a self-hosted
RPC node while still adding resilience and observability.

## Current Architecture

- `core/models.py`: strict Pydantic v2 configuration schema.
- `core/config.py`: YAML loading and config validation CLI.
- `core/state.py`: in-memory runtime state, counters, health snapshots, and event log.
- `core/prober.py`: background health probes using `eth_blockNumber`.
- `core/router.py`: aiohttp JSON-RPC proxy, failover loop, method routing, app startup.
- `ui/dashboard.py`: Rich TUI for node health, method routing, traffic, and live request logs.
- `tests/`: unit and integration tests with 100% line and branch coverage gates.

## Stack

- Language: Python 3.11+.
- Runtime HTTP: aiohttp.
- TUI: Rich.
- Schema: Pydantic v2 (`>=2.6,<3`).
- YAML: PyYAML.
- Testing: pytest, pytest-asyncio, pytest-cov, aioresponses.
- Quality: ruff and mypy in strict mode.

## Configuration Contract

- All config models must keep `model_config = ConfigDict(extra="forbid")`.
- Unknown YAML keys must be rejected, never silently accepted.
- Bad YAML or invalid schemas must propagate `pydantic.ValidationError` or
  `yaml.YAMLError` to the caller.
- Keep `routing_strategy` values aligned across config, models, router logic, TUI,
  and README examples.
- Method-specific routes live under `method_routes` and may override the global
  routing strategy for selected JSON-RPC methods.
- Do not reintroduce unused config fields. If a field is not implemented, document
  it in TODOs instead of accepting it in the schema.

## Runtime Behavior

- `POST /` accepts single JSON-RPC request objects and forwards them upstream.
- `GET /healthz` returns `{"ok": true}` for liveness checks.
- Failover should handle upstream `429`, `5xx`, network errors, timeouts, and bad
  upstream JSON bodies.
- Backoff must stay bounded and derived from `request_timeout_seconds`.
- Health probes run according to `probe_interval_seconds`; probe failures should
  not be confused with user request routing logs.
- Successful user requests should be observable in the event log as
  `request <method> -> <provider> (<latency>ms)`.

## TUI Expectations

- The TUI is read-only and must only consume `RouterState.snapshot()`.
- Header should show router name, probe interval, request timeout, strategy, bind
  address, status, and uptime.
- Node Health should show provider, status, ping, pressure, and success rate.
- Method Routing should show method-specific provider subsets and strategy overrides.
- Traffic & Performance should show TPS, failovers, total handled requests, and a
  short routing hint.
- Live Request Routing should prioritize user request routing events while still
  showing probe failures and failovers as operational context.

## Test And Quality Gates

Run these from the repository root before committing:

```bash
ruff check core ui tests
mypy --strict core ui
pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100
```

When Windows temp directory permissions interfere with pytest, use repo-local
temporary paths:

```bash
pytest -q --basetemp=.pytest_tmp -o cache_dir=.pytest_cache_local \
  --cov=core --cov=ui --cov-branch --cov-fail-under=100
```

After test runs, remove `.pytest_tmp` if it was created.

## Coverage Rules

- Maintain 100% line and branch coverage for `core` and `ui`.
- Add tests for every new branch, formatter path, error path, and routing path.
- Do not relax coverage thresholds.
- Prefer focused tests that prove behavior rather than broad snapshot assertions.

## Coding Guidelines

- Follow existing module boundaries and naming style.
- Prefer small, explicit functions over broad abstractions.
- Keep error handling transparent; do not swallow validation, YAML, or upstream
  routing errors unless the caller contract explicitly requires translation.
- Keep TUI formatting deterministic enough to test.
- Use structured config and Pydantic validation instead of ad hoc parsing.
- Avoid unrelated refactors in feature or bug-fix commits.

## Documentation Rules

- Keep `README.md` and `README_zh.md` aligned when user-facing behavior changes.
- Do not reference private planning files such as `AGENTS.md` or `plan.md` in the
  README files.
- Keep examples synchronized with `config.yaml` and implemented schema fields.
- Document TODO-only ideas as TODOs, not as working configuration fields.
