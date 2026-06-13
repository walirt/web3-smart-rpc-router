# Technical Innovation Notes

Web3 Smart RPC Router uses familiar Python infrastructure components, but the
implementation combines them into a focused local resilience layer for free
public RPC users. This document highlights the technical design choices that are
easy to miss when reviewing only the source tree.

## 1. Self-Healing Forwarding Pool

The forwarding path does not permanently trust the current health snapshot. If
all nodes are marked unhealthy, the router still builds a self-healing candidate
pool from the full configured provider chain and tries those nodes in priority
order.

Why it matters:

- A global outage can clear before the next probe tick.
- A stale unhealthy mark should not permanently block recovery.
- Clients still get a chance to succeed without waiting for manual intervention.

Relevant code:

- `core/router.py`: `_self_healing_pool()`, `forward_with_failover()`
- `tests/test_router.py`: failover and all-unhealthy proxy behavior

## 2. Snapshot-Isolated TUI

The TUI never reads or mutates live runtime objects directly. It consumes
`RouterState.snapshot()`, which deep-copies the state before rendering.

Why it matters:

- The dashboard cannot block request routing by holding the state lock.
- Terminal rendering cannot mutate provider health or counters.
- Runtime state and presentation stay cleanly separated.

Relevant code:

- `core/state.py`: `snapshot()`
- `ui/dashboard.py`: `render_frame()`, `dashboard_loop()`
- `tests/test_state.py`: snapshot decoupling tests
- `tests/test_dashboard.py`: read-only render behavior

## 3. Method-Aware RPC Routing

The router can route specific JSON-RPC methods to dedicated provider subsets.
This keeps the global endpoint simple for clients while allowing infrastructure
decisions to reflect method-specific needs.

Examples:

- Route `eth_getLogs` to archive-capable providers.
- Route `eth_sendRawTransaction` to broadcast-friendly providers.
- Let ordinary calls fall back to the global routing strategy.

Relevant code:

- `core/models.py`: `MethodRoute`
- `core/router.py`: `_route_for_payload()`
- `ui/dashboard.py`: Method Routing panel
- `config.yaml`: method route examples

## 4. Bounded Backoff Derived From User Timeout

Failover retries use a bounded exponential backoff derived from
`request_timeout_seconds`:

```text
base = request_timeout_seconds / 4
cap  = request_timeout_seconds * 4
```

Why it matters:

- The retry schedule stays proportional to the user's configured timeout.
- Failover avoids uncontrolled retry loops.
- The behavior is simple enough to document, test, and reason about.

Relevant code:

- `core/router.py`: `_backoff_delay()`
- `tests/test_router.py`: backoff behavior

## 5. Observable Local Resilience Without Running A Node

The project targets a middle ground between brittle single free RPC URLs and
expensive self-hosted RPC infrastructure:

| Option | Cost | Maintenance | Resilience | Observability |
|---|---:|---:|---:|---:|
| Single free RPC URL | Low | Low | Low | Low |
| Self-hosted RPC node | High | High | High | Medium |
| Smart RPC Router | Low | Low | Medium-High | High |

The technical novelty is not a new consensus or RPC protocol. It is the
combination of method-aware routing, health-aware failover, self-healing fallback,
and a read-only live dashboard in a local tool that ordinary free-RPC users can
run without operating a full node.
