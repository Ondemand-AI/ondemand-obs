"""Error tracking (Sentry SDK -> Better Stack Errors). No network: events are
caught by an in-memory transport."""

import types

import pytest
import sentry_sdk
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from sentry_sdk.transport import Transport

from ondemand_obs import errors

DSN = "https://public@errors.example.invalid/1"


class _Memory(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def events(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "errors-test")
    monkeypatch.setattr(errors, "_enabled", False)
    assert errors.configure_error_tracking("ignored") is True
    transport = _Memory()
    sentry_sdk.get_client().transport = transport
    yield transport.events
    sentry_sdk.get_client().close()
    errors._enabled = False


def _raise(exc):
    try:
        raise exc
    except BaseException as e:
        return e


def test_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(errors, "_enabled", False)
    assert errors.configure_error_tracking("svc") is False
    assert not errors.error_tracking_enabled()
    assert errors.capture_exception(ValueError("x")) is None


def test_event_carries_service_environment_and_no_tracing(events):
    client = sentry_sdk.get_client()
    assert client.options["traces_sample_rate"] == 0.0
    assert client.options["trace_propagation_targets"] == []
    assert client.options["include_local_variables"] is False

    event_id = errors.capture_exception(_raise(ValueError("boom")))

    assert event_id
    (event,) = events
    assert event["environment"] == "development"
    assert event["server_name"] == "errors-test"
    assert event["tags"]["service"] == "errors-test"
    assert event["exception"]["values"][-1]["type"] == "ValueError"


def test_links_the_active_otel_span(events):
    tracer = TracerProvider().get_tracer("t")
    with tracer.start_as_current_span("work") as span:
        errors.capture_exception(_raise(RuntimeError("in span")))
        ctx = span.get_span_context()

    (event,) = events
    assert event["contexts"]["trace"]["trace_id"] == trace.format_trace_id(ctx.trace_id)
    assert event["contexts"]["trace"]["span_id"] == trace.format_span_id(ctx.span_id)


def test_same_exception_is_reported_once(events):
    exc = _raise(KeyError("k"))
    assert errors.capture_exception(exc)
    assert errors.capture_exception(exc) is None
    # A different path (e.g. the excepthook) seeing the same object is dropped too.
    sentry_sdk.capture_exception(exc)
    assert len(events) == 1


def test_temporal_tags_from_the_activity_context(events, monkeypatch):
    import temporalio.activity

    info = types.SimpleNamespace(
        workflow_id="wf-123", workflow_run_id="run-456", workflow_type="DemoWorkflow",
        activity_type="process_company", activity_id="1", attempt=3, task_queue="demo",
    )
    monkeypatch.setattr(temporalio.activity, "info", lambda: info)

    errors.capture_exception(_raise(ValueError("in activity")), tags={"temporal.attempt": "3x"})

    tags = events[0]["tags"]
    assert tags["TemporalWorkflowID"] == "wf-123"
    assert tags["TemporalRunID"] == "run-456"
    assert tags["temporal.workflow_type"] == "DemoWorkflow"
    assert tags["temporal.activity_type"] == "process_company"
    assert tags["temporal.attempt"] == "3x"  # explicit tags win


def test_registered_secrets_are_scrubbed(events):
    # Imported here, not at module top: test_configure reloads ondemand_obs, and
    # the registry errors.py scrubs with is the one in sys.modules now.
    from ondemand_obs.instruments import register_secret

    register_secret("hunter2-vault-pass")
    errors.capture_exception(_raise(ValueError("login failed for hunter2-vault-pass")))

    value = events[0]["exception"]["values"][-1]["value"]
    assert "hunter2-vault-pass" not in value
    assert "[redacted]" in value


def test_error_logs_do_not_become_events(events):
    import logging

    logging.getLogger("robot").error("handled, logged, not an event")
    sentry_sdk.flush()
    assert events == []
