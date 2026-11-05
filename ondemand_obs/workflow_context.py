"""
Stamp Temporal's identifiers onto every span and log record.

Temporal identifies a Workflow Execution by a PAIR:

  Workflow ID  the business identifier the caller chooses. The portal starts
               workflows with `workflowId: run.id`, so process_runs.id IS the
               Workflow ID.
  Run ID       a server-generated UUID identifying ONE execution of that
               Workflow ID. A workflow retry, continue-as-new, cron tick or
               reset opens a new one under the same Workflow ID.

Temporal's own TracingInterceptor already writes TemporalWorkflowID and
TemporalRunID onto the spans IT creates. Everything else a robot emits — every
HTTP client span, every log line — carried neither, so correlating a run in
HyperDX meant full-text searching a UUID instead of filtering a field.

This span processor puts the same two attribute names on everything the process
emits, deliberately reusing Temporal's spelling rather than inventing a third
one. One name per identifier, across every service.

Since 0.1.14 it also stamps WHOSE run it is, so one robot's telemetry and errors
can be filtered by client:

  TemporalNamespace  the run's Temporal namespace. In production that is the
                     organization's code (executor.js starts each run in
                     `organization.code`); in development every organization
                     shares `dev`.
  OrganizationID     the portal's organization id (organizations.id), from the
                     run's WorkflowInput. Not in Temporal's info, so the worker
                     binds it per activity (bind_organization, called by
                     ondemand-ai's activity interceptor). A contextvar, so
                     concurrent activities on one pod never mix them up;
                     temporalio copies the context into sync activities' threads.
"""

import contextvars

from opentelemetry.sdk.trace import SpanProcessor

WORKFLOW_ID_KEY = "TemporalWorkflowID"
RUN_ID_KEY = "TemporalRunID"
NAMESPACE_KEY = "TemporalNamespace"
ORGANIZATION_ID_KEY = "OrganizationID"

_organization_id: contextvars.ContextVar = contextvars.ContextVar(
    "ondemand_obs_organization_id", default=None
)


def bind_organization(organization_id):
    """Mark the current task's telemetry and errors as belonging to this
    organization. Returns a token for reset_organization(). Empty values are
    ignored (the token then resets to the current value)."""
    return _organization_id.set(organization_id or _organization_id.get())


def reset_organization(token) -> None:
    try:
        _organization_id.reset(token)
    except Exception:
        pass


def current_organization_id():
    return _organization_id.get()


def _current_context():
    """
    Read the identifiers from whichever Temporal context is active.

    Returns (workflow_id, run_id, namespace), any of which may be None. Safe to
    call outside a workflow or activity — temporalio raises when there is no
    context, and there frequently isn't (worker startup, shutdown, background
    tasks).
    """
    try:
        from temporalio import activity

        info = activity.info()
        # `namespace` on recent temporalio; `workflow_namespace` (deprecated) before.
        namespace = getattr(info, "namespace", None) or getattr(info, "workflow_namespace", None)
        return info.workflow_id, info.workflow_run_id, namespace
    except Exception:
        pass

    try:
        from temporalio import workflow

        info = workflow.info()
        return info.workflow_id, info.run_id, info.namespace
    except Exception:
        return None, None, None


def _current_ids():
    """(workflow_id, run_id) — kept for callers of the pre-0.1.14 name."""
    workflow_id, run_id, _ = _current_context()
    return workflow_id, run_id


def current_attributes() -> dict:
    """Every identifier above that is known right now, by attribute name."""
    workflow_id, run_id, namespace = _current_context()
    attrs = {
        WORKFLOW_ID_KEY: workflow_id,
        RUN_ID_KEY: run_id,
        NAMESPACE_KEY: namespace,
        ORGANIZATION_ID_KEY: current_organization_id(),
    }
    return {k: v for k, v in attrs.items() if v}


class TemporalIdSpanProcessor(SpanProcessor):
    """Adds the run identifiers (current_attributes) to every span at start."""

    def on_start(self, span, parent_context=None):
        # Never overwrite: on spans the TracingInterceptor created, its values
        # are authoritative.
        attrs = span.attributes or {}
        for key, value in current_attributes().items():
            if key not in attrs:
                span.set_attribute(key, value)

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class TemporalIdLogFilter:
    """
    Logging filter that attaches the same identifiers to every record.

    Installed as a filter rather than a Formatter change so it reaches the OTLP
    handler as structured attributes, not as text baked into the message.
    """

    def filter(self, record):
        for key, value in current_attributes().items():
            setattr(record, key, value)
        return True
