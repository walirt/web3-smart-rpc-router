# CyOps Build Plan 1 - Configuration Contract

## Goal

Establish the strict YAML configuration contract for Web3 Smart RPC Router.
This phase makes configuration validation reproducible before adding runtime
proxy behavior.

## Scope

Included:

- Pydantic v2 schema for global settings and RPC nodes.
- YAML loading with clear error propagation.
- A validated sample `config.yaml`.
- Focused tests for valid and invalid configuration paths.
- Initial README usage notes.

Out of scope:

- Runtime proxying.
- Background health probes.
- TUI rendering.
- WebSocket transport.
- Authentication, API-key management, or persistence.

## Acceptance Criteria

| ID | Requirement |
|---|---|
| AC-1 | Repository has `core/`, `tests/`, and minimal package scaffolding. |
| AC-2 | `requirements.txt` includes Pydantic v2, PyYAML, pytest, pytest-asyncio, pytest-cov, ruff, mypy, and types-PyYAML. |
| AC-3 | `core/models.py` defines strict Pydantic models with `extra="forbid"`. |
| AC-4 | `RoutingStrategy` supports `priority`, `round_robin`, `lowest_latency`, and `failover`. |
| AC-5 | `RouterConfig` rejects duplicate provider names and duplicate priorities. |
| AC-6 | `core/config.py` exposes `load_config()` and `parse_config_dict()`. |
| AC-7 | Bad YAML propagates `yaml.YAMLError`; invalid schema propagates `pydantic.ValidationError`. |
| AC-8 | `config.yaml` loads successfully and exercises the required fields. |
| AC-9 | Tests cover missing fields, invalid URLs, invalid strategies, duplicate providers, duplicate priorities, and unknown keys. |
| AC-10 | `ruff`, `mypy --strict core`, and coverage-gated pytest pass. |

## Implementation Steps

1. Create the base package layout.
2. Add pinned runtime and test dependencies.
3. Implement strict Pydantic models in `core/models.py`.
4. Implement YAML loading in `core/config.py`.
5. Add a working `config.yaml` sample.
6. Add schema and loader tests.
7. Document install, validation, and test commands in `README.md`.
8. Run the quality gates and commit the phase.

## Verification Matrix

| Area | Command or Check | Success Signal |
|---|---|---|
| Config model tests | `pytest -q tests/test_models.py` | All schema cases pass |
| Config loader tests | `pytest -q tests/test_config.py` | YAML and CLI paths pass |
| Coverage | `pytest -q --cov=core --cov-branch --cov-fail-under=100` | 100% coverage gate passes |
| Lint | `ruff check core tests` | No diagnostics |
| Types | `mypy --strict core` | No type errors |

## Delivered Artifacts

- `core/models.py`
- `core/config.py`
- `config.yaml`
- `tests/conftest.py`
- `tests/test_models.py`
- `tests/test_config.py`
- `requirements.txt`
- Initial README instructions
