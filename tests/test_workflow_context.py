"""Run identifiers on spans, logs and errors: namespace and organization (0.1.14)."""

import asyncio
import contextvars
import logging
import types
from concurrent.futures import ThreadPoolExecutor

from ondemand_obs import workflow_context as wc


def _fake_activity(monkeypatch, **fields):
    info = types.SimpleNamespace(
        workflow_id="wf-1", workflow_run_id="run-1", **fields
    )
    import temporalio.activity

    monkeypatch.setattr(temporalio.activity, "info", lambda: info)


def test_namespace_from_activity_info(monkeypatch):
    _fake_activity(monkeypatch, namespace="gse")
    assert wc.current_attributes() == {
        "TemporalWorkflowID": "wf-1",
        "TemporalRunID": "run-1",
        "TemporalNamespace": "gse",
    }


def test_namespace_falls_back_to_deprecated_field(monkeypatch):
    _fake_activity(monkeypatch, workflow_namespace="ondemand")
    assert wc.current_attributes()["TemporalNamespace"] == "ondemand"


def test_organization_bound_and_reset(monkeypatch):
    _fake_activity(monkeypatch, namespace="ondemand")
    token = wc.bind_organization("org-123")
    try:
        assert wc.current_attributes()["OrganizationID"] == "org-123"
    finally:
        wc.reset_organization(token)
    assert "OrganizationID" not in wc.current_attributes()


def test_empty_organization_is_ignored():
    token = wc.bind_organization("")
    assert wc.current_organization_id() is None
    wc.reset_organization(token)


def test_organization_reaches_sync_activity_threads():
    # temporalio runs sync activities via copy_context().run in a thread pool.
    token = wc.bind_organization("org-thread")
    try:
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(ctx.run, wc.current_organization_id).result() == "org-thread"
    finally:
        wc.reset_organization(token)


def test_concurrent_tasks_keep_their_own_organization():
    async def run(org):
        token = wc.bind_organization(org)
        try:
            await asyncio.sleep(0.01)
            return wc.current_organization_id()
        finally:
            wc.reset_organization(token)

    async def main():
        return await asyncio.gather(run("a"), run("b"))

    assert asyncio.run(main()) == ["a", "b"]


def test_log_filter_stamps_namespace_and_organization(monkeypatch):
    _fake_activity(monkeypatch, namespace="ondemand")
    token = wc.bind_organization("org-9")
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)
        assert wc.TemporalIdLogFilter().filter(record)
        assert record.TemporalNamespace == "ondemand"
        assert record.OrganizationID == "org-9"
        assert record.TemporalWorkflowID == "wf-1"
    finally:
        wc.reset_organization(token)


def test_span_processor_does_not_overwrite(monkeypatch):
    _fake_activity(monkeypatch, namespace="ondemand")

    class Span:
        attributes = {"TemporalNamespace": "kept"}

        def __init__(self):
            self.set = {}

        def set_attribute(self, k, v):
            self.set[k] = v

    span = Span()
    wc.TemporalIdSpanProcessor().on_start(span)
    assert "TemporalNamespace" not in span.set
    assert span.set["TemporalWorkflowID"] == "wf-1"
