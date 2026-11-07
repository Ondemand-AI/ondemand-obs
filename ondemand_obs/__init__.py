"""
ondemand-obs — OpenTelemetry observability for the Ondemand platform.

Usage in workers (automatic via ondemand-python SDK — no direct call needed):

    from ondemand_obs import configure_observability
    configure_observability("my-robot")

Set HYPERDX_API_KEY in env to enable. No-op if the key is absent.

Error tracking (Better Stack Errors, via the Sentry SDK) is separate and
opt-in: set SENTRY_DSN. See errors.py.
"""

import logging
import os
import threading
from importlib.metadata import PackageNotFoundError, version as _dist_version
from typing import Optional

from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from .config import ObsConfig
from .traces import setup_traces
from .metrics import setup_metrics
from .logs import setup_logs
from .instruments import register_secret, setup_instruments
from .errors import (
    capture_exception,
    configure_error_tracking,
    error_tracking_enabled,
    flush_errors,
)
from .workflow_context import (
    bind_organization,
    current_organization_id,
    reset_organization,
)

logger = logging.getLogger("ondemand_obs")


def _dist(name: str) -> Optional[str]:
    try:
        return _dist_version(name)
    except PackageNotFoundError:
        return None


__version__ = _dist("ondemand-obs") or "0.0.0.dev0"

# Library versions are stamped onto every span, metric and log as resource
# attributes. Robots may upgrade ondemand-ai at container start, so the image
# tag no longer implies which library version is running — this is the only
# reliable way to answer "which robots are on which version?" in HyperDX.
_LIB_ATTRS = {
    "ondemand.ai.version": _dist("ondemand-ai"),
    "ondemand.obs.version": __version__,
}

_configured = False
_lock = threading.Lock()
_log_handler = None


def configure_observability(service_name: str, config: Optional[ObsConfig] = None) -> None:
    global _configured, _log_handler

    if config is None:
        config = ObsConfig()

    # Independent of HYPERDX_API_KEY: errors go to Better Stack through the
    # Sentry SDK whenever SENTRY_DSN is set. Idempotent, never raises.
    configure_error_tracking(service_name)

    if not config.api_key:
        return

    with _lock:
        if _configured:
            return

        attrs = {
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name),
        }
        env = os.environ.get("DEPLOYMENT_ENVIRONMENT", "")
        if env:
            attrs["deployment.environment"] = env

        attrs.update({k: v for k, v in _LIB_ATTRS.items() if v})

        resource = Resource.create(attrs)

        headers = {"authorization": config.api_key}

        # Publish the endpoint we actually use, so anything that reads the env
        # later sends to the same place: ondemand-ai forwards
        # OTEL_EXPORTER_OTLP_ENDPOINT to the GPU pods and serverless workers it
        # launches, which otherwise fall back to their own (older) default.
        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", config.endpoint)

        setup_traces(config.endpoint, headers, resource)
        setup_metrics(config.endpoint, headers, resource)
        _, handler = setup_logs(config.endpoint, headers, resource)
        setup_instruments(log_handler=handler)

        # Suppress HTTP client loggers — auto-instrumentation already creates
        # spans for these calls, so the Python log lines are pure duplication.
        for noisy_logger in ("httpx", "httpcore", "urllib3", "requests"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

        _log_handler = handler
        _configured = True

        logger.info(
            f"ondemand-obs: observability enabled for service '{service_name}' "
            f"(ondemand-ai={_LIB_ATTRS['ondemand.ai.version'] or 'n/a'}, "
            f"ondemand-obs={__version__})"
        )


def is_configured() -> bool:
    return _configured


def get_otlp_log_handler() -> Optional[logging.Handler]:
    """Return the OTLP LoggingHandler, or None if not configured."""
    return _log_handler


def get_temporal_tracing_interceptor():
    """Return a Temporal TracingInterceptor. Requires ondemand-obs[temporal]."""
    from .temporal import get_tracing_interceptor
    return get_tracing_interceptor()
