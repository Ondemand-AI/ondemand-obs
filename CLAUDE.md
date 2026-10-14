# ondemand-obs — Observability Layer

OpenTelemetry observability for the Ondemand platform. Ships traces, metrics, and logs to HyperDX (or any OTLP-compatible backend).

- **PyPI name:** `ondemand-obs`
- **Import path:** `ondemand_obs`
- **Current version:** 0.1.10 (see `pyproject.toml`)
- **Python:** ≥ 3.9
- **Branch:** `main`

## How it's used

Robots never call this directly — the `ondemand-ai[worker]` SDK depends on `ondemand-obs[temporal,llm]` and calls `configure_observability(service_name)` automatically at worker startup. It is a **no-op when `HYPERDX_API_KEY` is absent**, so local runs without the key are silent and safe.

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
├── instruments.py   # httpx/requests + LLM SDK auto-instrumentation, body capture policy
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
- `[llm]` — OpenLLMetry instrumentors for the OpenAI and Anthropic SDKs (Python >= 3.10)
- `[dev]` — pytest + pytest-asyncio + httpx + requests + openai

## What HTTP calls record (body policy, since 0.1.10)

Every request body is recorded, on every host. What never reaches HyperDX is **our credentials**.

| Call | Recorded |
|---|---|
| Robot → any host, via httpx or requests | Span + `http.request.body` (up to 64 KB) + `http.request.body.size`. JSON and form-encoded bodies have keys that *contain* password/senha/secret/token/api_key/authorization/credential `[redacted]`. Multipart and binary bodies (file uploads) record the size only. |
| Robot → `localhost` / `127.0.0.1` / `::1` | Nothing. That is the Bitwarden CLI server: `/unlock` carries the master password, item responses carry every credential we hold. |
| Any host answering 4xx/5xx | A WARN log on `ondemand_obs.http_errors` with the response body (JSON redacted, text/plain kept, HTML/binary skipped) **and the request body that provoked it**, in the same trace. That logger does NOT propagate to root, so it never reaches the portal console. |
| OpenAI / Anthropic SDK (`[llm]`) | A `gen_ai` span: model, input/output tokens, finish reason, tool call names, latency, errors. The gen_ai span keeps prompt/completion text off (`TRACELOOP_TRACE_CONTENT=false`), but the **prompt is recorded** as the request body of the httpx span underneath. |

**Our credentials are removed by value, not by field name.** `register_secret(value)` adds a value to a registry; every recorded body is scrubbed of each registered value, raw and URL-/JSON-encoded, before truncation. A site's password field can be called anything (`txtSenha`, `j_password`, `campo_x`), so a key list alone would leak one eventually. Sources:

- At setup: every env var whose **name** contains one of the secret parts above (`HYPERDX_API_KEY`, `BW_PASSWORD`, `BW_CLIENTSECRET`, `ONDEMAND_WEBHOOK_SECRET`, `R2_SECRET_ACCESS_KEY`, ...).
- At runtime: `ondemand-ai`'s `bw_get_item()` registers each item's password, TOTP seed and custom field values as it fetches them.
- Values shorter than 6 characters are ignored (scrubbing `1234` from every body destroys data and protects nothing).

Headers are never recorded, so `Authorization` / `x-webhook-secret` are not in scope.

Playwright traffic is never seen: the browser's requests do not go through httpx.

Before 0.1.9 the request-body hook read `stream._buffer`, a field httpx never had (it is `_stream`), so **no body was ever captured**. `tests/test_instruments.py` drives the real instrumentor end to end so this cannot silently regress again.

The response body cannot be a span attribute: the httpx instrumentor ends the span before the client reads the body. That is why error bodies are logs, emitted from a wrapper around the public `Client.send` / `AsyncClient.send`.

## Tests

```bash
pip install -e ".[dev,temporal,llm]"
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
