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
"""

from opentelemetry.sdk.trace import SpanProcessor

WORKFLOW_ID_KEY = "TemporalWorkflowID"
RUN_ID_KEY = "TemporalRunID"


def _current_ids():
    """
    Read the identifiers from whichever Temporal context is active.

    Returns (workflow_id, run_id), either of which may be None. Safe to call
    outside a workflow or activity — temporalio raises when there is no context,
    and there frequently isn't (worker startup, shutdown, background tasks).
    """
    try:
        from temporalio import activity

        info = activity.info()
        return info.workflow_id, info.workflow_run_id
    except Exception:
        pass

    try:
        from temporalio import workflow

        info = workflow.info()
        return info.workflow_id, info.run_id
    except Exception:
        return None, None


class TemporalIdSpanProcessor(SpanProcessor):
    """Adds the Temporal identifier pair to every span at start."""

    def on_start(self, span, parent_context=None):
        workflow_id, run_id = _current_ids()
        # Never overwrite: on spans the TracingInterceptor created, its values
        # are authoritative.
        attrs = span.attributes or {}
        if workflow_id and WORKFLOW_ID_KEY not in attrs:
            span.set_attribute(WORKFLOW_ID_KEY, workflow_id)
        if run_id and RUN_ID_KEY not in attrs:
            span.set_attribute(RUN_ID_KEY, run_id)

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class TemporalIdLogFilter:
    """
    Logging filter that attaches the same pair to every record.

    Installed as a filter rather than a Formatter change so it reaches the OTLP
    handler as structured attributes, not as text baked into the message.
    """

    def filter(self, record):
        workflow_id, run_id = _current_ids()
        if workflow_id:
            setattr(record, WORKFLOW_ID_KEY, workflow_id)
        if run_id:
            setattr(record, RUN_ID_KEY, run_id)
        return True
