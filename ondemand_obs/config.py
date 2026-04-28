import os
from dataclasses import dataclass, field

HYPERDX_OTLP_ENDPOINT = "https://in-otel.hyperdx.io"


@dataclass
class ObsConfig:
    endpoint: str = field(default_factory=lambda: os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", HYPERDX_OTLP_ENDPOINT
    ))
    api_key: str = field(default_factory=lambda: os.environ.get("HYPERDX_API_KEY", ""))
