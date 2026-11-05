"""
Error tracking: exceptions to Better Stack Errors through the Sentry SDK.

OpenTelemetry stays the telemetry pipeline (traces, metrics, logs). Better Stack
Error tracking only ingests Sentry-SDK events, though: an OTel span exception
event or an ERROR log never becomes an Error-tracking entry. So exceptions go
out a second way, and only exceptions:

  - no Sentry tracing (traces_sample_rate=0) and no trace headers added to
    outgoing calls (trace_propagation_targets=[]),
  - no events from log records (LoggingIntegration event_level=None): a robot
    that logs an error and then raises would otherwise report it twice,
  - no auto-enabled integrations (httpx, boto3, openai, ...): OTel already
    instruments those, and two wrappers on one client is a risk with no payoff,
  - no local variables in stack frames: robots hold vault credentials in
    locals, and every string in the event is scrubbed of our registered
    secrets anyway (the same registry the body capture uses).

Opt-in: nothing happens unless SENTRY_DSN is set. Each event is linked to the
active OTel span (trace_id/span_id, as Better Stack documents) and tagged with
the Temporal identifier pair under the platform's spelling, TemporalWorkflowID
and TemporalRunID, plus the workflow/activity type and attempt.

Temporal catches activity exceptions and turns them into failures, so Sentry's
global hooks never see them. ondemand-ai's worker interceptor calls
capture_exception() for those explicitly.
"""

import logging
import os
import socket
import threading

logger = logging.getLogger("ondemand_obs")

DSN_ENV = "SENTRY_DSN"
# Used only when SENTRY_DSN is unset. Robots get SENTRY_DSN from their own Errors
# app (k8s secret ondemand-sentry, key = process id) and this one from the shared
# "Automations (sem app própria)" app, so a robot without an app of its own still
# reports. Two names because KEDA's ScaledJob schema rejects a duplicate env name.
FALLBACK_DSN_ENV = "SENTRY_DSN_FALLBACK"

# Set on an exception once it has been reported, so a later path (the
# excepthook, the worker's crash handler) does not report the same object again.
_CAPTURED_ATTR = "__ondemand_error_captured__"

_enabled = False
_lock = threading.Lock()


def _temporal_tags() -> dict:
    """Temporal context of the current task, as Sentry tags. Empty outside one."""
    try:
        from temporalio import activity

        info = activity.info()
        return {
            "TemporalWorkflowID": info.workflow_id,
            "TemporalRunID": info.workflow_run_id,
            "temporal.workflow_type": info.workflow_type,
            "temporal.activity_type": info.activity_type,
            "temporal.activity_id": info.activity_id,
            "temporal.attempt": str(info.attempt),
            "temporal.task_queue": info.task_queue,
        }
    except Exception:
        pass
    try:
        from temporalio import workflow

        info = workflow.info()
        return {
            "TemporalWorkflowID": info.workflow_id,
            "TemporalRunID": info.run_id,
            "temporal.workflow_type": info.workflow_type,
            "temporal.attempt": str(info.attempt),
            "temporal.task_queue": info.task_queue,
        }
    except Exception:
        return {}


def _link_otel(event: dict) -> None:
    """Point the event at the active OTel span (Better Stack's documented hook).

    Overwrites rather than setdefault: the Sentry SDK always fills
    contexts.trace with its own (unrelated) propagation ids before before_send
    runs, so setdefault would never take effect.
    """
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return
    event.setdefault("contexts", {})["trace"] = {
        "type": "trace",
        "trace_id": trace.format_trace_id(ctx.trace_id),
        "span_id": trace.format_span_id(ctx.span_id),
    }


def _scrub_strings(value):
    from .instruments import _scrub

    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_strings(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_strings(v) for v in value)
    return value


def _before_send(event, hint):
    exc = (hint or {}).get("exc_info", (None, None, None))[1]
    if exc is not None and getattr(exc, _CAPTURED_ATTR, False):
        return None  # already reported by capture_exception()
    try:
        _link_otel(event)
    except Exception:
        pass
    try:
        tags = event.setdefault("tags", {})
        for k, v in _temporal_tags().items():
            if v not in (None, "") and k not in tags:
                tags[k] = v
    except Exception:
        pass
    try:
        event = _scrub_strings(event)
    except Exception:
        pass
    return event


def _lib_tags() -> dict:
    from importlib.metadata import PackageNotFoundError, version

    tags = {}
    for dist, tag in (("ondemand-ai", "ondemand.ai.version"), ("ondemand-obs", "ondemand.obs.version")):
        try:
            tags[tag] = version(dist)
        except PackageNotFoundError:
            pass
    return tags


def configure_error_tracking(service_name: str, dsn: str = None) -> bool:
    """Start reporting exceptions to Better Stack Errors. Idempotent.

    Returns True when error tracking is active. A no-op returning False when no
    DSN is configured (SENTRY_DSN, else SENTRY_DSN_FALLBACK) or sentry-sdk is
    not installed; it never raises, because error reporting must not stop a
    robot from starting.
    """
    global _enabled

    dsn = dsn or os.environ.get(DSN_ENV, "") or os.environ.get(FALLBACK_DSN_ENV, "")
    if not dsn:
        return False

    with _lock:
        if _enabled:
            return True
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
        except ImportError:
            logger.warning("ondemand-obs: SENTRY_DSN is set but sentry-sdk is not installed")
            return False

        from .instruments import _register_env_secrets

        _register_env_secrets()

        service = os.environ.get("OTEL_SERVICE_NAME") or service_name
        environment = (
            os.environ.get("SENTRY_ENVIRONMENT")
            or os.environ.get("DEPLOYMENT_ENVIRONMENT")
            or "development"
        )
        try:
            sentry_sdk.init(
                dsn=dsn,
                environment=environment,
                release=os.environ.get("SENTRY_RELEASE") or None,
                server_name=service,
                traces_sample_rate=0.0,
                trace_propagation_targets=[],
                auto_enabling_integrations=False,
                integrations=[LoggingIntegration(level=logging.INFO, event_level=None)],
                include_local_variables=False,
                before_send=_before_send,
            )
            sentry_sdk.set_tag("service", service)
            sentry_sdk.set_tag("host", socket.gethostname())
            for k, v in _lib_tags().items():
                sentry_sdk.set_tag(k, v)
        except Exception as e:
            logger.warning(f"ondemand-obs: error tracking disabled, Sentry init failed: {e}")
            return False

        _enabled = True

    logger.info(f"ondemand-obs: error tracking enabled for service '{service}' ({environment})")
    return True


def error_tracking_enabled() -> bool:
    return _enabled


def capture_exception(exc: BaseException, *, tags: dict = None, contexts: dict = None):
    """Report `exc` once. Returns the event id, or None when disabled, already
    reported, or dropped. Never raises."""
    if not _enabled or exc is None or getattr(exc, _CAPTURED_ATTR, False):
        return None
    try:
        import sentry_sdk

        event_id = sentry_sdk.capture_exception(
            exc,
            tags={k: v for k, v in (tags or {}).items() if v not in (None, "")},
            contexts=contexts or {},
        )
    except Exception:
        return None
    try:
        setattr(exc, _CAPTURED_ATTR, True)
    except Exception:
        pass
    return event_id


def flush_errors(timeout: float = 5.0) -> None:
    """Block until queued events are sent (before a deliberate process exit)."""
    if not _enabled:
        return
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=timeout)
    except Exception:
        pass
