import os
from dataclasses import dataclass, field

# Ondemand's telemetry hub (the VPS collector behind nginx). It exports everything to
# both hyperdx.io and our self-hosted HyperDX v2, so senders only know one address.
# Was https://in-otel.hyperdx.io until 0.1.11.
HYPERDX_OTLP_ENDPOINT = "https://otel.ondemand-ai.com.br"


@dataclass
class ObsConfig:
    endpoint: str = field(default_factory=lambda: os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", HYPERDX_OTLP_ENDPOINT
    ))
    api_key: str = field(default_factory=lambda: os.environ.get("HYPERDX_API_KEY", ""))
