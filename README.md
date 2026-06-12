# Web3 Smart RPC Router

A local-only, cyberpunk-styled Web3 Smart RPC Router. The system fronts multiple free
public RPC endpoints and provides transparent failover against `429` and `5xx` upstream
responses, exposed behind a Rich TUI dashboard.

## Status

**Phase 1 — In Progress.** This phase delivers the configuration contract:
a Pydantic v2 schema for `RouterConfig` plus a strict YAML loader. Business logic
(proxy, health prober, TUI) is added in later phases.

## Quickstart (Phase 1)

```bash
pip install -r requirements.txt
pytest -q
python -m core.config config.yaml   # validate a config file
```

More detail (install, test, coverage gate, validate-config examples) will be added
when Phase 1 reaches the documentation AC.
