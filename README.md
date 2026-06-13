# Web3 Smart RPC Router

English | [中文](README_zh.md)

> _Built with [CyOps](https://docs.cysic.xyz/cysic-ai/cysic-automation/cyops/)_

A local JSON-RPC gateway for Ethereum-style RPC traffic. It fronts multiple
public upstream RPC endpoints, hides transient provider failures from clients, and shows live health, failover, and traffic state in a terminal UI.

## What It Solves

Free public RPC endpoints are useful, but they are brittle. A single endpoint
can rate-limit with `429`, degrade with `5xx`, time out, or briefly disappear.
Most local scripts and wallets expect one stable JSON-RPC URL, so every provider
hiccup becomes an application failure.

This router provides one stable local endpoint:

```text
client -> http://127.0.0.1:<listen_port>/ -> best available upstream RPC node
```

When an upstream returns a transient failure, the router retries another node
with bounded exponential backoff and returns the successful upstream response to
the caller. The client does not see the intermediate `429` or `5xx`.

## Feature Checklist

| Feature | Status | Evidence |
|---|---:|---|
| Strict YAML configuration contract | Done | `core/models.py`, `core/config.py`, `config.yaml` |
| Unknown config keys rejected | Done | Pydantic `extra="forbid"` models |
| Local JSON-RPC proxy | Done | `core/router.py`, `POST /` |
| Liveness endpoint | Done | `GET /healthz -> {"ok": true}` |
| Provider failover on `429` and `5xx` | Done | `forward_with_failover()` and router tests |
| Failover on network, timeout, and bad JSON bodies | Done | mocked upstream tests with `aioresponses` |
| Bounded exponential backoff | Done | `request_timeout_seconds / 4` base, `* 4` cap |
| Routing strategies | Done | `priority`, `round_robin`, `lowest_latency`, `failover` |
| Background health prober | Done | `core/prober.py`, `eth_blockNumber` probes |
| In-memory state and snapshots | Done | `core/state.py`, `RouterState.snapshot()` |
| Read-only Rich TUI dashboard | Done | `ui/dashboard.py`, `--with-tui` |
| 100% line and branch coverage | Done | `pytest --cov-branch --cov-fail-under=100` |
| Strict typing and linting | Done | `mypy --strict core ui`, `ruff check core ui tests` |

## Quickstart

Install dependencies:

```bash
pip install -r requirements.txt
```

Validate the sample config:

```bash
python -m core.config config.yaml
```

Run the router with the terminal dashboard:

```bash
python -m core.router config.yaml --with-tui
```

Send JSON-RPC requests to:

```text
http://127.0.0.1:8545/
```

Check liveness:

```bash
curl http://127.0.0.1:8545/healthz
```

Expected response:

```json
{"ok": true}
```

## Configuration

`config.yaml` is the single source of truth for runtime behavior:

```yaml
global:
  listen_port: 8545
  probe_interval_seconds: 5.0
  request_timeout_seconds: 10.0
  routing_strategy: priority
  max_retries: 3

method_routes:
  eth_getLogs:
    providers: [cloudflare-eth]
    routing_strategy: priority

rpc_nodes:
  - provider: cloudflare-eth
    url: https://cloudflare-ethereum.com
    priority: 1
    weight: 1
    headers: {}
```

### How to Use `config.yaml`

1. Copy or edit `config.yaml` in the repository root.
2. Set `global.listen_port` to the local port you want the router to expose.
3. Set `global.routing_strategy` to choose how the router selects upstreams.
4. Add one `rpc_nodes` entry per upstream RPC provider.
5. Give every node a unique `provider` name and a unique `priority` number.
6. Optionally add `method_routes` entries for JSON-RPC methods that should use
   a specific provider subset or strategy.
7. Validate before running:

```bash
python -m core.config config.yaml
```

8. Start the router with that config:

```bash
python -m core.router config.yaml --with-tui
```

Field reference:

| Field | Where | Meaning |
|---|---|---|
| `listen_port` | `global` | Local HTTP port exposed by the router |
| `probe_interval_seconds` | `global` | How often the background prober checks each upstream |
| `request_timeout_seconds` | `global` | Upstream request timeout and backoff basis |
| `routing_strategy` | `global` | One of `priority`, `round_robin`, `lowest_latency`, `failover` |
| `max_retries` | `global` | Reserved retry budget in the config contract |
| `method_routes.<method>.providers` | `method_routes` | Provider names allowed for a specific JSON-RPC method |
| `method_routes.<method>.routing_strategy` | `method_routes` | Optional strategy override for that method |
| `provider` | `rpc_nodes[]` | Unique display and state key for the upstream |
| `url` | `rpc_nodes[]` | Upstream JSON-RPC HTTP(S) endpoint |
| `priority` | `rpc_nodes[]` | Lower number means higher priority |
| `weight` | `rpc_nodes[]` | Reserved weight field in the config contract |
| `headers` | `rpc_nodes[]` | Optional HTTP headers sent to that upstream |

Example with two upstreams:

```yaml
global:
  listen_port: 8545
  probe_interval_seconds: 5.0
  request_timeout_seconds: 10.0
  routing_strategy: priority
  max_retries: 3

method_routes:
  eth_getLogs:
    providers: [archive]
    routing_strategy: priority

  eth_sendRawTransaction:
    providers: [tx-broadcast, fallback]
    routing_strategy: failover

rpc_nodes:
  - provider: archive
    url: https://primary.example/rpc
    priority: 1
    weight: 1
    headers: {}

  - provider: tx-broadcast
    url: https://tx.example/rpc
    priority: 2
    weight: 1
    headers: {}

  - provider: fallback
    url: https://fallback.example/rpc
    priority: 3
    weight: 1
    headers:
      X-Demo: local-router
```

Validation rules are strict:

- Top-level keys outside `global` and `rpc_nodes` are rejected.
- Unknown node/global fields are rejected.
- Provider names and priorities must be unique.
- `method_routes` may only reference providers declared in `rpc_nodes`.
- URLs must use `http` or `https`.
- `routing_strategy` belongs under `global`, not under individual nodes.
- Timeouts and probe intervals must be positive.
- Bad YAML and invalid schemas propagate as errors instead of being swallowed.

## Runtime Behavior

### Endpoints

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/` | JSON-RPC passthrough to the selected upstream |
| `GET` | `/healthz` | Local liveness probe, always returns HTTP 200 while running |

### Failover Contract

The router treats these upstream outcomes as retryable:

- HTTP `429`
- HTTP `5xx`
- `aiohttp.ClientError`
- `asyncio.TimeoutError`
- JSON bodies that are not objects
- JSON parsing/content-type failures

Backoff is bounded exponential:

```text
base  = request_timeout_seconds / 4
cap   = request_timeout_seconds * 4
delay = min(base * 2 ** (attempt - 1), cap)
```

On total exhaustion, the local proxy returns:

```json
{"error": "no healthy upstream"}
```

with HTTP `503`.

### Routing Strategies

| Strategy | Selection rule |
|---|---|
| `priority` | Pick the healthy node with the lowest priority number |
| `round_robin` | Rotate across healthy nodes in priority order |
| `lowest_latency` | Pick the healthy node with the lowest probed latency |
| `failover` | Always start with the first healthy node in priority order |

If every node is currently marked unhealthy, the forwarding path still tries the
full configured chain in priority order. This gives the service a self-healing
path after a global outage clears.

### Method-Based Routing

`method_routes` lets specific JSON-RPC methods use a provider subset and an
optional strategy override. This is useful when different methods have different
infrastructure needs:

- `eth_getLogs` can be routed to archive-capable nodes.
- `eth_sendRawTransaction` can be routed to transaction broadcast providers.
- Unmatched methods use the global `routing_strategy` and the full node list.

Only single JSON-RPC request objects are routed this way today. Batch request
routing can be added later if needed.

## Dashboard

Run with `--with-tui` to launch the Rich dashboard in the same event loop as the
router. The dashboard is read-only: it only consumes `RouterState.snapshot()` and
never mutates request flow.

Dashboard panels:

- Header: active status and uptime.
- Node Health: provider, status, ping, strategy, quota bar, and success-rate estimate.
- Method Routing: method-specific provider subsets and optional strategy overrides.
- Traffic & Performance: current TPS, failover count, total requests, and an
  automatic traffic-shift hint.
- Live Self-Healing Logs: timestamped probe failures, failovers, and request
  events.

The UI is implemented in `ui/dashboard.py` with `rich.layout.Layout`,
`rich.panel.Panel`, and `rich.table.Table`.

## Architecture

```text
config.yaml
    |
    v
core.config.load_config()
    |
    v
core.models.RouterConfig
    |
    +--> core.state.RouterState
    |       - NodeStats per provider
    |       - request counters
    |       - bounded event log
    |       - snapshot() for read-only consumers
    |
    +--> core.router.make_app()
    |       - POST / JSON-RPC proxy
    |       - GET /healthz
    |       - strategy-aware upstream selection
    |       - bounded failover and backoff
    |
    +--> core.prober.prober_loop()
    |       - periodic eth_blockNumber probes
    |       - latency and health updates
    |
    +--> ui.dashboard.dashboard_loop()
            - read-only terminal dashboard
```

Module boundaries are deliberately small:

- `core/models.py`: schema only.
- `core/config.py`: YAML loading and validation only.
- `core/state.py`: in-memory runtime state and locking.
- `core/router.py`: request routing, failover, and aiohttp application wiring.
- `core/prober.py`: background health checks.
- `ui/dashboard.py`: rendering and terminal refresh loop.

## Engineering Quality

The implementation emphasizes reproducibility and defensive behavior:

- Pydantic v2 models reject unknown configuration keys.
- Validation errors are not swallowed or replaced with generic messages.
- Runtime state writes use an `asyncio.Lock` via `RouterState.transaction()`.
- TUI rendering uses deep-copy snapshots instead of live mutable state.
- Upstream calls are isolated behind one `aiohttp.ClientSession`.
- The app-owned upstream client is created and cleaned up through aiohttp cleanup
  contexts when tests or callers do not inject one.
- The prober isolates per-tick exceptions so one bad node cannot stop health
  checks.
- Tests mock upstream RPC traffic; no test depends on real public endpoints.
- No persistent secrets or database state are written.

## Verification

Run all commands from the repository root.

```bash
python -m pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100
ruff check core ui tests
mypy --strict core ui
```

Current verified result:

```text
105 passed
Required test coverage of 100% reached. Total coverage: 100.00%
ruff: All checks passed
mypy: Success: no issues found
```

On Windows sandboxed environments where pytest cannot write to the default user
temp directory, use a local base temp:

```bash
python -m pytest -q --basetemp=.pytest_tmp -o cache_dir=.pytest_cache_local \
  --cov=core --cov=ui --cov-branch --cov-fail-under=100
```

## Test Coverage Map

| Test file | What it proves |
|---|---|
| `tests/test_models.py` | Schema constraints, enum values, URL validation, duplicate checks |
| `tests/test_config.py` | YAML loading, invalid YAML propagation, CLI validation path |
| `tests/test_state.py` | Locking, snapshots, event log cap, TPS counters, state seeding |
| `tests/test_router.py` | Selection strategies, failover, backoff, handler errors, app lifecycle |
| `tests/test_prober.py` | Probe success/failure, loop cancellation, exception isolation |
| `tests/test_dashboard.py` | Rendered layout, dashboard labels, logs, demo state, loop exit |
| `tests/test_integration.py` | In-process router startup and real local HTTP requests |

## Project Layout

```text
.
|-- core/
|   |-- __init__.py
|   |-- config.py       # YAML loader and config validation CLI
|   |-- models.py       # Pydantic configuration schema
|   |-- prober.py       # Background upstream health checks
|   |-- router.py       # aiohttp proxy, routing, failover, app entry point
|   `-- state.py        # In-memory state, locking, snapshots
|-- ui/
|   |-- __init__.py
|   `-- dashboard.py    # Rich terminal dashboard
|-- tests/
|   |-- conftest.py
|   |-- test_config.py
|   |-- test_dashboard.py
|   |-- test_integration.py
|   |-- test_models.py
|   |-- test_prober.py
|   |-- test_router.py
|   `-- test_state.py
|-- config.yaml
|-- pytest.ini
|-- requirements.txt
|-- README.md
`-- README_zh.md
```

## Dependencies

Runtime:

- `pydantic`
- `PyYAML`
- `aiohttp`
- `rich`

Testing and quality:

- `pytest`
- `pytest-asyncio`
- `pytest-aiohttp`
- `pytest-cov`
- `aioresponses`
- `pytest-timeout`
- `ruff`
- `mypy`
- `types-PyYAML`

## Scope Boundaries

Included:

- Local JSON-RPC gateway.
- Mocked upstream tests.
- In-memory health and traffic state.
- Terminal dashboard.
- Strict schema validation.

Not included:

- Public internet deployment.
- Authentication or API-key management.
- Persistent state, SQLite, Redis, or external databases.
- Real live-RPC benchmark tests.
- WebSocket transports (`ws://` / `wss://`) and `eth_subscribe` proxying.
- FastAPI, uvicorn, httpx, React, Vue, or browser UI.

TODO:

- Add full WebSocket proxy support for `ws://` and `wss://` upstreams.

## License

MIT
