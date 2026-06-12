# Web3 Smart RPC Router

A local-only, cyberpunk-styled Web3 Smart RPC Router. The system fronts multiple
free public RPC endpoints and provides transparent failover against `429` and
`5xx` upstream responses, exposed behind a Rich TUI dashboard.

## Status

**Phase 1 — Configuration contract.** A Pydantic v2 schema (`core/models.py`)
defines `RouterConfig` with strict `extra="forbid"` validation; a YAML loader
(`core/config.py`) parses configs into it. A sample `config.yaml` exercises
every required field. 100% line + branch coverage on `core/models.py` and
`core/config.py`.

**Phase 2 — Live router.** The full proxy stack now ships:

- **`core/state.py`** — in-memory `RouterState` (per-node `NodeStats`,
  rolling 1s `tps_1s`, 256-entry event log) guarded by an
  `asyncio.Lock`. `snapshot()` is a `copy.deepcopy` decoupled from live
  mutations so the TUI is a pure observer.
- **`core/router.py`** — `aiohttp.web` proxy at `POST /` + liveness
  probe at `GET /healthz`. Strategy-aware `select_node` honours
  `priority`, `round_robin`, `lowest_latency`, and `failover`.
  `forward_with_failover` walks the sorted chain with bounded
  exponential backoff on `429` / `5xx` / network / JSON errors and
  never surfaces a transient failure to the caller. The proxy's
  self-heal fallback retries every node when the healthy pool
  empties so the system survives a global outage.
- **`core/prober.py`** — background `eth_blockNumber` prober that
  updates each `NodeStats` once per `probe_interval_seconds`,
  with per-tick exception isolation.
- **`ui/dashboard.py`** — read-only 3-row `rich` TUI (header / body
  table / live event tape) with the cyberpunk palette
  (`#00ff9c` neon green, `#ff2bd6` magenta, `#7df9ff` cyan).
- **`core/__main__`** — `python -m core.router <config.yaml> [--with-tui]`
  wires the runner, prober, and optional TUI together on one
  event loop and shuts them down cleanly on `Ctrl+C`.

## Quickstart (Phase 2)

```bash
pip install -r requirements.txt                   # install runtime + test + dev deps
pytest -q                                         # 90 tests, 100% line+branch cov
python -m core.config config.yaml                 # validate a config file
python -m core.router config.yaml --with-tui      # run the router + TUI
```

Each command is explained in detail below.

## Install

Install the pinned runtime, test, and dev-tooling dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt` pins:

- **Runtime** — `pydantic>=2.6,<3`, `PyYAML>=6.0`, `aiohttp>=3.9,<3.13`, `rich>=13.7,<14`
- **Tests** — `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=4.1`, `pytest-aiohttp>=1.0`, `aioresponses>=0.7`, `pytest-timeout>=2.1`
- **Dev tooling** — `mypy>=1.10`, `ruff>=0.5`, `types-PyYAML>=6.0`

A Python 3.11+ interpreter is required.

## Run the tests

The full test suite is the verification gate for Phase 2. From the repo
root:

```bash
pytest -q
```

Expected output:

```
.................................................................  [100%]
90 passed in 1.0s
```

### Coverage gate (must hit 100%)

The same suite with the coverage threshold applied is the canonical
acceptance command — it will exit non-zero if any module in `core/` or
`ui/` falls below 100% line or branch coverage:

```bash
python -m pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100
```

Expected footer:

```
core/__init__.py       0      0      0      0   100%
core/config.py        27      0      6      0   100%
core/models.py        50      0     10      0   100%
core/prober.py        59      0      6      0   100%
core/router.py       154      0     34      0   100%
core/state.py         67      0      6      0   100%
ui/__init__.py         0      0      0      0   100%
ui/dashboard.py       86      0      6      0   100%
TOTAL                443      0     68      0   100%
Required test coverage of 100% reached. Total coverage: 100.00%
```

### Lint and type-check

Both run with their built-in defaults (no `pyproject.toml` / `ruff.toml` /
`mypy.ini` is needed for the current code):

```bash
ruff check core ui tests
mypy --strict core ui
```

Both commands must exit `0`.

## Validate a config file (`core.config`)

The `__main__` block of `core/config.py` is the Phase 1 entry point for
validating a YAML config against the `RouterConfig` schema:

```bash
python -m core.config config.yaml
```

prints:

```
OK: loaded 2 rpc_node(s); listen_port=8545
```

## Phase 2 — Running the router

The router is a single-process service that fronts your configured
upstream RPC nodes and transparently fails over on `429` / `5xx` / network
errors. The canonical entry point is:

```bash
python -m core.router <config.yaml> [--with-tui]
```

| Flag         | Meaning                                                                |
|--------------|------------------------------------------------------------------------|
| `config`     | Path to a YAML router config file (see `core/models.py`).             |
| `--with-tui` | Also launch the cyberpunk Rich TUI dashboard in this same process.   |

The router listens on `127.0.0.1:<global.listen_port>`. `Ctrl+C` (or
`SIGTERM` on POSIX) triggers a clean shutdown of the runner, prober,
and TUI task.

### Endpoints

| Method | Path        | Behaviour                                                |
|--------|-------------|----------------------------------------------------------|
| `POST` | `/`         | JSON-RPC passthrough. Body: `{"jsonrpc": "2.0", ...}`.    |
| `GET`  | `/healthz`  | Liveness probe. Always returns `{"ok": true}` (HTTP 200). |

### Failover contract

* `429` (rate-limited) and any `5xx` upstream response → failover.
* `aiohttp.ClientError` / `asyncio.TimeoutError` → failover.
* Non-mapping JSON body (e.g. a JSON array) → failover.
* On total exhaustion, the proxy returns `503 {"error": "no healthy upstream"}`.

The backoff schedule is **bounded exponential**:

```
delay = min(base * 2 ** (attempt - 1), cap)
base = request_timeout_seconds / 4
cap  = request_timeout_seconds * 4
```

The `caller` is never exposed to the upstream's transient errors.

### TUI layout

Current dashboard panels:

- Header: `🚀 Web3 Smart RPC Router (v1.0)` with active status and `HH:MM:SS` uptime.
- Node Health & Method Routing: `PROVIDER`, `STATUS`, `PING`, `ROUTING STRATEGY`,
  `QUOTA USED`, and `SUCCESS RATE`.
- Traffic & Performance: current TPS sparkline, failover count, total requests,
  and the active traffic-shift hint.
- Live Self-Healing Logs: timestamped request, probe-failure, and failover events.

```
╭─ WEB3 SMART RPC ROUTER | uptime 30.0s | TPS(1s) 1.00 | req 7 | ok 5 | failover 2 ─╮
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Provider ┃ URL                   ┃ Pri ┃ Strategy ┃ Healthy ┃ Latency(ms) ┃ ConsecFail ┃ LastError                   ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ alpha    │ https://alpha.test/rpc │   1 │ PRIORITY │ YES     │       12.5 │          0 │                            │
│ beta     │ https://beta.test/rpc  │   2 │ FAILOVER │ NO      │         n/a │          3 │ upstream returned HTTP 503 │
└──────────┴────────────────────────┴─────┴──────────┴─────────┴─────────────┴────────────┴────────────────────────────┘
╭─ Live event tape ────────────────────────────────────────────────────────────────────╮
│ failover alpha -> beta                                                                    │
│ probe-fail beta upstream returned HTTP 503                                                │
│ failover beta -> alpha                                                                    │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

The dashboard refreshes once per second and is a **read-only observer**;
it never mutates `RouterState` and never takes its lock.

### In-memory state

`RouterState` is the single source of truth for the running router:

```python
@dataclass
class NodeStats:
    provider: str
    url: str
    priority: int
    routing_strategy: RoutingStrategy
    healthy: bool = True
    latency_ms: Optional[float] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_probed_at: Optional[float] = None
```

`RouterState` exposes `nodes`, `round_robin_index`, `total_requests`,
`total_success`, `total_failovers`, a rolling `tps_1s`, and a
256-entry `event_log` of `failover` / `probe-fail` lines.

### Sample transcript

```
$ python -m core.router config.yaml --with-tui
[INFO] router: router listening on http://127.0.0.1:8545 (tui=True)

# (TUI renders, refreshes once per second; see the layout above)

^C
router: shutting down
```

## Project layout

```
.
├── core/                    # Phase 1 + Phase 2 deliverable
│   ├── __init__.py
│   ├── config.py            # load_config, parse_config_dict, __main__ block
│   ├── models.py            # GlobalSettings, RoutingStrategy, RpcNode, RouterConfig
│   ├── state.py             # RouterState, NodeStats, transaction(), snapshot()
│   ├── router.py            # ProxyHandler, select_node, forward_with_failover, main_async
│   ├── prober.py            # probe_once, prober_loop (background health checks)
│   └── __main__             # entry point: python -m core.router <config> [--with-tui]
├── ui/
│   ├── __init__.py
│   └── dashboard.py         # render_frame, dashboard_loop, __main__ demo
├── tests/                   # Phase 1 + Phase 2 test suite
│   ├── __init__.py
│   ├── conftest.py          # valid_global_dict, valid_node_dict, tmp_config_file
│   ├── test_models.py       # 22 cases covering every Pydantic constraint
│   ├── test_config.py       # 10 cases covering the loader + __main__ block
│   ├── test_state.py        # 13 cases covering state + transaction + snapshot
│   ├── test_router.py       # 19 cases covering select_node + forward + handler
│   ├── test_prober.py       #  7 cases covering probe_once + prober_loop
│   ├── test_dashboard.py    # 11 cases covering render_frame + dashboard_loop
│   └── test_integration.py  #  8 cases covering main_async + end-to-end proxy
├── config.yaml              # validated sample
├── pytest.ini
├── requirements.txt
├── README.md                # this file
└── .gitignore
```

## Out of scope

The following are **not** part of Phase 2 and intentionally absent from
the code:

- A real head-to-head against live public RPC endpoints (Cloudflare,
  Ankr, PublicNode). All upstream traffic in this phase is mocked via
  `aioresponses`. A separate "smoke test against the real internet"
  step can land in a future phase.
- Docker, systemd unit, or any deployment artifact.
- Authentication, request signing, API keys, per-client rate-limit
  budgets.
- Persistent state (no SQLite, no JSON snapshot to disk). `RouterState`
  is in-memory only and resets on process restart.
- Replacing the existing Phase 1 `RouterConfig` schema or YAML
  contract. Phase 2 consumes it read-only.
- Adding extra dependencies beyond the four pinned in
  `requirements.txt`. In particular, no `fastapi`, `uvicorn`, `httpx`,
  `sqlalchemy`, or `redis`.
