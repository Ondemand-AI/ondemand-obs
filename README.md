# ondemand-obs

OpenTelemetry observability layer for the [Ondemand AI](https://ondemand-ai.com.br) platform. Ships traces, metrics, and logs to HyperDX (or any OTLP-compatible backend).

## Usage

Set `HYPERDX_API_KEY` in your environment. That's it — the `ondemand-ai[worker]` SDK calls `configure_observability()` automatically at worker startup.

```bash
HYPERDX_API_KEY=your-key OTEL_SERVICE_NAME=my-robot python -m src.main
```

## Install

```bash
pip install ondemand-obs           # base (traces, metrics, logs, httpx/requests auto-instrument)
pip install "ondemand-obs[temporal]"  # + Temporal TracingInterceptor
```

## Environment variables

| Variable | Required | Default |
|---|---|---|
| `HYPERDX_API_KEY` | Yes (disables if absent) | — |
| `OTEL_SERVICE_NAME` | No | passed to `configure_observability()` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `https://in-otel.hyperdx.io` |
