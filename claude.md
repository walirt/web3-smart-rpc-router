# Project Standards — web3-smart-rpc-router

This file defines the engineering conventions that Claude (and any other agent) must
follow when working in this repository. It is loaded automatically by Claude Code via
its memory system.

## Project summary

A local-only, cyberpunk-styled Web3 Smart RPC Router. The system fronts multiple free
public RPC endpoints and provides transparent failover against `429` and `5xx` upstream
responses. Phase 1 (this phase) implements only the configuration contract; later phases
add a proxy, a health prober, and a Rich TUI.

## Stack

- Language: Python 3.11+ (container ships 3.12).
- Schema: Pydantic v2 (`>=2.6,<3`).
- YAML: PyYAML.
- Testing: pytest + pytest-asyncio + pytest-cov (asyncio_mode = "auto").
- Quality: ruff (line-length 100, target py311), mypy in `--strict` mode.

## Phase 1 invariants (non-negotiable)

1. `core/models.py` and `core/config.py` must hit 100% line + branch coverage. Add a
   test for any new branch — do not relax the threshold.
2. Pydantic models use `model_config = ConfigDict(extra="forbid")` so any unknown
   YAML key is rejected. Never silently accept extras.
3. Bad YAML or invalid schemas MUST raise `pydantic.ValidationError` (or
   `yaml.YAMLError`) and propagate to the caller. Never swallow these errors.
4. Do not introduce `aiohttp`, `fastapi`, `rich`, `httpx`, or `uvicorn` in
   `requirements.txt` during Phase 1. They are added in later phases.
5. The `ui/` package remains an empty `__init__.py` until Phase 4. Do not scaffold
   TUI code in earlier phases.

## Commit discipline

- Every task in the plan ends with a `git commit` step.
- Commit messages use Conventional Commits: `feat:`, `test:`, `chore:`, `docs:`.
- One logical change per commit; small, reviewable history.

## Test-running conventions

- Always run `pytest` from the repo root.
- Coverage gate: `pytest -q --cov=core --cov-branch --cov-fail-under=100`.
- Lint gate: `ruff check core tests` and `mypy --strict core` both must exit 0.
