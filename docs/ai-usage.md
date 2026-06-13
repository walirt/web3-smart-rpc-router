# CyOps-Assisted Development

This project was developed with CyOps as the assisted engineering environment.
CyOps was used to iterate on requirements, implementation, tests, review, and
documentation while keeping the runtime router deterministic and local-first.

## Overview

Web3 Smart RPC Router is not an AI chatbot or an autonomous runtime agent. It is
a local infrastructure tool for routing Ethereum-style JSON-RPC traffic across
multiple public providers. CyOps was used during the build process, not as a
runtime dependency.

That separation is intentional:

- The router should keep working without network access to any AI service.
- JSON-RPC forwarding should stay predictable and easy to debug.
- The runtime dependency set should remain small and infrastructure-focused.

## Development Workflow

The assisted workflow followed a practical engineering loop:

1. Clarify the behavior being added or changed.
2. Update the smallest relevant module.
3. Add focused tests for new branches, errors, and routing paths.
4. Run linting, strict typing, and coverage-gated tests.
5. Review the diff for scope, stale docs, and user-facing consistency.
6. Keep README and Chinese README changes aligned when behavior changes.

## Where The Work Shows Up

The CyOps-assisted build process is reflected in ordinary repository artifacts:

| Area | Files |
|---|---|
| Strict configuration contract | `core/models.py`, `core/config.py` |
| JSON-RPC proxy and failover | `core/router.py` |
| Background health checks | `core/prober.py` |
| Runtime state and snapshots | `core/state.py` |
| Terminal dashboard | `ui/dashboard.py` |
| Behavior coverage | `tests/` |
| User-facing docs | `README.md`, `README_zh.md` |

## Quality Checks

The standard verification commands are:

```text
ruff check core ui tests
mypy --strict core ui
pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100
```

Current local result:

```text
112 passed
Required test coverage of 100% reached. Total coverage: 100.00%
```

## Runtime Boundary

No LLM calls are made by the router at runtime. The router only performs local
configuration validation, HTTP JSON-RPC forwarding, health probing, state
updates, and terminal rendering.

This keeps the product aligned with its goal: a lightweight local gateway that
improves free-RPC reliability without the cost or maintenance burden of running
a full self-hosted RPC node.
