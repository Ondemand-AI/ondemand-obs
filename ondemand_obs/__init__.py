"""
ondemand-obs — OpenTelemetry observability for the Ondemand platform.

Usage in workers (automatic via ondemand-python SDK — no direct call needed):

    from ondemand_obs import configure_observability
    configure_observability("my-robot")

Set HYPERDX_API_KEY in env to enable. No-op if the key is absent.
"""

import logging
import os
import threading

from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from .config import ObsConfig
from .traces import setup_traces
from .metrics import setup_metrics
from .logs import setup_logs
from .instruments import setup_instruments

logger = logging.getLogger("ondemand_obs")

_configured = False
_lock = threading.Lock()
_log_handler = None


def configure_observability(service_name: str, config: ObsConfig | None = None) -> None:
    global _configured, _log_handler

    if config is None:
        config = ObsConfig()

    if not config.api_key:
        return

    with _lock:
        if _configured:
            return

        resource = Resource.create({
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name),
        })

        headers = {"authorization": config.api_key}

        setup_traces(config.endpoint, headers, resource)
        setup_metrics(config.endpoint, headers, resource)
        _, handler = setup_logs(config.endpoint, headers, resource)
        setup_instruments()

        _log_handler = handler
        _configured = True

        logger.info(f"ondemand-obs: observability enabled for service '{service_name}'")


def is_configured() -> bool:
    return _configured


def get_otlp_log_handler() -> logging.Handler | None:
    """Return the OTLP LoggingHandler, or None if not configured."""
    return _log_handler


def get_temporal_tracing_interceptor():
    """Return a Temporal TracingInterceptor. Requires ondemand-obs[temporal]."""
    from .temporal import get_tracing_interceptor
    return get_tracing_interceptor()
