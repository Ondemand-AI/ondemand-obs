# ondemand-obs — Observability Layer

OpenTelemetry observability for the Ondemand platform. Ships traces, metrics, and logs to HyperDX (or any OTLP-compatible backend).

- **PyPI name:** `ondemand-obs`
- **Import path:** `ondemand_obs`
- **Current version:** 0.1.5 (see `pyproject.toml`)
- **Python:** ≥ 3.9
- **Branch:** `main`

## How it's used

Robots never call this directly — the `ondemand-ai[worker]` SDK depends on `ondemand-obs[temporal]` and calls `configure_observability(service_name)` automatically at worker startup. It is a **no-op when `HYPERDX_API_KEY` is absent**, so local runs without the key are silent and safe.

```python
from ondemand_obs import configure_observability
configure_observability("my-robot")  # idempotent, thread-safe
```

## Package layout

```
ondemand_obs/
├── __init__.py      # configure_observability() — entry point, guards re-config
├── config.py        # ObsConfig dataclass (endpoint, api_key from env)
├── traces.py        # TracerProvider + OTLP HTTP exporter
├── metrics.py       # MeterProvider setup
├── logs.py          # LoggerProvider + log handler
├── instruments.py   # httpx/requests auto-instrumentation (captures HTTP bodies)
└── temporal.py      # Temporal TracingInterceptor (requires [temporal] extra)
```

## Environment variables

| Variable | Required | Default |
|---|---|---|
| `HYPERDX_API_KEY` | Yes — observability disabled if absent | — |
| `OTEL_SERVICE_NAME` | No | passed to `configure_observability()` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `https://in-otel.hyperdx.io` |

## Extras

- `[temporal]` — adds `temporalio>=1.7.0` for the Temporal `TracingInterceptor`
- `[dev]` — pytest + pytest-asyncio

## Tests

```bash
pip install -e ".[dev,temporal]"
pytest
```

## Release process

1. Bump version in `pyproject.toml`
2. Push to `main`
3. Push a tag `vX.Y.Z` → GitHub Actions publishes to PyPI via Trusted Publishing (`.github/workflows/publish.yml`)
