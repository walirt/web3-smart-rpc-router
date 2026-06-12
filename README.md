# Web3 Smart RPC Router

A local-only, cyberpunk-styled Web3 Smart RPC Router. The system fronts multiple
free public RPC endpoints and provides transparent failover against `429` and
`5xx` upstream responses, exposed behind a Rich TUI dashboard.

## Status

**Phase 1 — Configuration contract.** This phase delivers:

- A Pydantic v2 schema (`core/models.py`) describing the full `RouterConfig`
  contract: `GlobalSettings`, `RoutingStrategy` enum, `RpcNode`, and
  `RouterConfig` with strict `extra="forbid"` validation.
- A strict YAML loader (`core/config.py`) exposing exactly two public
  functions: `load_config(path)` and `parse_config_dict(raw)`.
- A **command-line entry point** delivered as the `__main__` block of
  `core/config.py`: `python -m core.config <path-to-config.yaml>` validates
  a YAML file against the schema, prints a one-line `OK:` summary on
  success, and exits non-zero with a `FAIL:` line on schema/IO errors.
- A sample `config.yaml`, an exhaustive `tests/test_models.py` /
  `tests/test_config.py` pair, and 100% line+branch coverage on `core/`.

Business logic (proxy, health prober, TUI) is added in later phases — see
[Out of Scope](#out-of-scope) below.

## Quickstart (Phase 1)

```bash
pip install -r requirements.txt        # install runtime + test + dev deps
pytest -q                              # run the full test suite (32 tests, 100% cov)
python -m core.config config.yaml      # validate a config file via the __main__ block
```

That's the whole Phase 1 surface area. Each command is explained in detail
below.

## Install

Install the pinned runtime, test, and dev-tooling dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt` pins:

- **Runtime** — `pydantic>=2.6,<3`, `PyYAML>=6.0`
- **Tests** — `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=4.1`
- **Dev tooling** — `mypy>=1.10`, `ruff>=0.5`, `types-PyYAML>=6.0`

A Python 3.11+ interpreter is required (the schema uses `from __future__
import annotations` and PEP 604-style unions through `typing.Annotated`).

## Run the tests

The full test suite is the verification gate for Phase 1. From the repo
root:

```bash
pytest -q
```

Expected output:

```
................................                                         [100%]
32 passed in 0.24s
```

### Coverage gate (must hit 100%)

The same suite with the coverage threshold applied is the canonical
acceptance command — it will exit non-zero if `core/` falls below 100%
line or branch coverage:

```bash
python -m pytest -q --cov=core --cov-branch --cov-fail-under=100
```

Expected footer:

```
core/__init__.py       0      0      0      0   100%
core/config.py        27      0      6      0   100%
core/models.py        52      0     10      0   100%
TOTAL                 79      0     16      0   100%
Required test coverage of 100% reached. Total coverage: 100.00%
```

### Lint and type-check

Both run with their built-in defaults (no `pyproject.toml` / `ruff.toml` /
`mypy.ini` is needed for the current code):

```bash
ruff check core tests
mypy --strict core
```

Both commands must exit `0`.

## Validate a config file (`__main__` block)

The `__main__` block of `core/config.py` is the **Phase 1 command-line
entry point** for validating a YAML config against the
`RouterConfig` schema. It is a one-liner:

```bash
python -m core.config <path-to-config.yaml>
```

### Behaviour

| Input                                  | Exit | Stdout / stderr                                                                                  |
| -------------------------------------- | ---- | ------------------------------------------------------------------------------------------------ |
| Valid config file                      | `0`  | `OK: loaded N rpc_node(s); listen_port=<port>` (one line on stdout)                              |
| File does not exist                    | `1`  | `FAIL: FileNotFoundError: [Errno 2] No such file or directory: '<path>'` (stderr)                |
| Empty / non-mapping YAML               | `1`  | `FAIL: ValidationError: ...` (pydantic)                                                          |
| Schema-invalid YAML (bad port, dup id) | `1`  | `FAIL: ValidationError: <error list>` (pydantic)                                                 |
| Missing CLI argument                   | `2`  | `usage: python -m core.config <path-to-config.yaml>` (stderr)                                    |

### Try it on the bundled sample

```bash
python -m core.config config.yaml
```

prints:

```
OK: loaded 2 rpc_node(s); listen_port=8545
```

The sample exercises every required field in `core/models.py`: a `global`
block with `listen_port`, `probe_interval_seconds`,
`request_timeout_seconds`, and `max_retries`, plus two `rpc_nodes` (one
HTTP, one HTTPS) using the `priority` routing strategy with distinct
providers and distinct priorities.

### Use it from CI

A pre-deploy check that the config is well-formed is one shell line:

```bash
python -m core.config config.yaml || exit 1
```

## Project layout

```
.
├── core/                    # Phase 1 deliverable: schema + YAML loader
│   ├── __init__.py
│   ├── config.py            # load_config, parse_config_dict, __main__ block
│   └── models.py            # GlobalSettings, RoutingStrategy, RpcNode, RouterConfig
├── ui/                      # Phase 4 — TUI dashboard (empty for now)
│   └── __init__.py
├── tests/                   # Phase 1 test suite
│   ├── __init__.py
│   ├── conftest.py          # valid_global_dict, valid_node_dict, tmp_config_file
│   ├── test_models.py       # 22 cases covering every Pydantic constraint
│   └── test_config.py       # 10 cases covering the loader + __main__ block
├── config.yaml              # validated sample
├── requirements.txt
├── README.md                # this file
└── .gitignore
```

## Out of scope

The following are **not** part of Phase 1 and intentionally absent from
the code:

- Phase 2 — the proxy / routing engine, `aiohttp` or `FastAPI` server.
- Phase 3 — the health prober, exponential backoff queue, in-memory state.
- Phase 4 — the Rich TUI dashboard (`rich.live`, `rich.table`,
  `rich.layout`).
- Docker, CI, or any deployment artifact.
- Authentication, rate-limit budget tracking, request caching, metrics.

No `rich`, `aiohttp`, `fastapi`, `httpx`, or `uvicorn` dependency will be
added in Phase 1.
