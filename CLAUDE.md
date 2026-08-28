# ondemand-obs — Observability Layer

OpenTelemetry observability for the Ondemand platform. Ships traces, metrics, and logs to HyperDX (or any OTLP-compatible backend).

- **PyPI name:** `ondemand-obs`
- **Import path:** `ondemand_obs`
- **Current version:** 0.1.8 (see `pyproject.toml`)
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

## Identifier vocabulary — Workflow ID vs Run ID

The platform uses **Temporal's own names**, everywhere, for the two identifiers
of a Workflow Execution. Temporal identifies one by a **pair**:

| Name | What it is | Where it lives |
|---|---|---|
| **Workflow ID** | The business identifier we choose. `executor.js` starts every workflow with `workflowId: run.id`, so `process_runs.id` **is** the Workflow ID. Stable across retries. | `process_runs.id`, `step_runs.workflow_id`, `approval_requests.workflow_id`, `scheduled_runs.last_workflow_id`, `artifacts/{workflow_id}/…`, `/api/webhooks/supervisor/{workflow_id}` |
| **Run ID** | A server-generated UUID identifying **one execution** of that Workflow ID. A new one appears on workflow retry, continue-as-new, cron tick, reset, or re-run under the same Workflow ID. | `process_runs.temporal_run_id`, `step_runs.temporal_run_id` |

- **API JSON:** `workflow_id` and `run_id`.
- **HyperDX:** `TemporalWorkflowID` and `TemporalRunID` on every span *and* every
  log, in every service — the same spelling Temporal's own `TracingInterceptor`
  writes, so one filter follows a run portal → API → robot.
- **Python:** `current_workflow_id()` and `current_temporal_run_id()` in
  `ondemand.shared.run_context`. Env var: `ONDEMAND_WORKFLOW_ID`.

Never reintroduce a bare `run_id` meaning the Workflow ID. That collision is
what this vocabulary exists to end.

`step_runs` is keyed on `(workflow_id, temporal_run_id, step_id)` with
`NULLS NOT DISTINCT`, so a workflow retry gets its own step rows instead of
overwriting the previous attempt's.
