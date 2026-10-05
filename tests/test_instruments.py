import http.server
import json
import logging
import threading

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ondemand_obs import instruments
from ondemand_obs.instruments import (
    _host_matches,
    _httpx_request_hook,
    _install_error_response_logging,
    _log_error_response,
    _redact,
)


class _Span:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _RequestInfo:
    """Shape of what HTTPXClientInstrumentor passes to the request hook."""

    def __init__(self, url, body):
        request = httpx.Request("POST", url, content=body)
        self.url = request.url
        self.stream = request.stream


def _capture(url, body):
    span = _Span()
    _httpx_request_hook(span, _RequestInfo(url, body))
    return span.attributes.get("http.request.body")


STEP_REPORT = json.dumps({
    "action": "STEP_REPORT",
    "payload": {"step_report": {
        "step_id": "baixar", "step_status": "completed", "summary": "12 extratos",
        "records": [{"cnpj": "12.345.678/0001-90", "arquivo": "extrato_EMPRESA_X.pdf"}],
        "auth": {"access_token": "abc", "client_secret": "def"},
    }},
}).encode()


# --------------------------------------------------------------------------- #
# Request bodies: our API only, secrets redacted
# --------------------------------------------------------------------------- #

def test_our_api_body_captured_with_only_secrets_redacted():
    body = _capture("https://api.ondemand-ai.com.br/api/webhooks/supervisor/wf1", STEP_REPORT)
    step = json.loads(body)["payload"]["step_report"]
    assert step["summary"] == "12 extratos"
    assert step["records"][0]["arquivo"] == "extrato_EMPRESA_X.pdf"
    assert step["auth"] == {"access_token": "[redacted]", "client_secret": "[redacted]"}
    assert "abc" not in body and "def" not in body


def test_dev_api_is_allowed():
    assert _capture("https://api-dev.ondemand-ai.com.br/x", b'{"a": 1}') == '{"a": 1}'


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1/chat/completions",
    "https://www.bb.com.br/login",
    "https://cav.receita.fazenda.gov.br/",
    "https://ondemand-ai.com.br.evil.net/",
])
def test_other_hosts_never_captured(url):
    assert _capture(url, b'{"j_password": "segredo"}') is None


def test_non_json_not_captured():
    assert _capture("https://api.ondemand-ai.com.br/x", b"user=a&txtSenha=b") is None


def test_redact_matches_key_substrings_case_insensitively():
    out = _redact({"txtSenha": "x", "J_PASSWORD": "y", "nested": [{"apiKey": 1, "ok": 2}]})
    assert out == {"txtSenha": "[redacted]", "J_PASSWORD": "[redacted]",
                   "nested": [{"apiKey": "[redacted]", "ok": 2}]}


def test_wildcard_does_not_match_lookalike():
    patterns = ("*.ondemand-ai.com.br",)
    assert _host_matches("API.ONDEMAND-AI.COM.BR", patterns)
    assert not _host_matches("evilondemand-ai.com.br", patterns)


# --------------------------------------------------------------------------- #
# Error response bodies: any host, logged to HyperDX only
# --------------------------------------------------------------------------- #

class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        status, ctype, body = self.routes[self.path]
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def error_log(monkeypatch):
    handler = _Collect()
    logger = instruments._error_logger
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    logger.setLevel(logging.WARNING)
    _install_error_response_logging()
    return handler.records


def test_error_json_body_logged_redacted(server, error_log):
    _Handler.routes = {"/fail": (400, "application/json",
                                 b'{"error": "CNPJ invalido", "token": "abc123"}')}
    response = httpx.post(f"{server}/fail", json={"x": 1})
    assert response.json()["token"] == "abc123"  # the caller still gets the real body
    [record] = error_log
    body = record.__dict__["http.response.body"]
    assert "CNPJ invalido" in body
    assert "abc123" not in body
    assert record.__dict__["http.response.status_code"] == 400


def test_error_logger_does_not_reach_root(server, error_log):
    """The root logger feeds the portal console; third-party errors must not."""
    root = _Collect()
    logging.getLogger().addHandler(root)
    try:
        _Handler.routes = {"/fail": (500, "text/plain", b"upstream down")}
        httpx.get(f"{server}/fail")
    finally:
        logging.getLogger().removeHandler(root)
    assert [r.__dict__["http.response.body"] for r in error_log] == ["upstream down"]
    assert root.records == []


def test_success_and_html_errors_not_logged(server, error_log):
    _Handler.routes = {
        "/ok": (200, "application/json", b'{"a": 1}'),
        "/html": (500, "text/html", b"<html>erro</html>"),
    }
    httpx.get(f"{server}/ok")
    httpx.get(f"{server}/html")
    assert error_log == []


def test_streaming_error_is_not_consumed(server, error_log):
    _Handler.routes = {"/fail": (400, "application/json", b'{"error": "x"}')}
    with httpx.Client() as client:
        with client.stream("GET", f"{server}/fail") as response:
            assert error_log == []
            assert response.read() == b'{"error": "x"}'


@pytest.mark.asyncio
async def test_async_client_error_logged(server, error_log):
    _Handler.routes = {"/fail": (422, "application/json", b'{"detail": "bad"}')}
    async with httpx.AsyncClient() as client:
        await client.post(f"{server}/fail", json={})
    assert len(error_log) == 1


def test_log_error_response_never_raises():
    _log_error_response(object(), object())


# --------------------------------------------------------------------------- #
# End to end with the real httpx instrumentor
# --------------------------------------------------------------------------- #

def _provider():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_request_body_with_real_instrumentor(server, monkeypatch):
    """The 0.1.8 hook read a field httpx never had and captured nothing for
    months. Drive the real instrumentor and transport so that cannot recur."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    monkeypatch.setattr(instruments, "_REQUEST_BODY_HOSTS", ("127.0.0.1",))
    _Handler.routes = {"/hook": (200, "application/json", b"{}")}
    provider, exporter = _provider()

    instrumentor = HTTPXClientInstrumentor()
    # A singleton: configure_observability() in another test may already have
    # instrumented httpx, which would make this instrument() a no-op.
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    instrumentor.instrument(tracer_provider=provider, request_hook=_httpx_request_hook)
    try:
        httpx.post(f"{server}/hook", content=STEP_REPORT)
    finally:
        instrumentor.uninstrument()

    [span] = exporter.get_finished_spans()
    body = json.loads(span.attributes["http.request.body"])
    assert body["payload"]["step_report"]["auth"]["access_token"] == "[redacted]"


# --------------------------------------------------------------------------- #
# [llm]: OpenAI SDK call becomes a gen_ai span, without the text
# --------------------------------------------------------------------------- #

CHAT_COMPLETION = json.dumps({
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-4o-2024-08-06",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "Conta 3.1.02"}}],
    "usage": {"prompt_tokens": 1840, "completion_tokens": 62, "total_tokens": 1902},
}).encode()


def test_openai_call_traced_without_content(server, monkeypatch):
    openai = pytest.importorskip("openai")
    from opentelemetry.instrumentation.openai import OpenAIInstrumentor

    monkeypatch.delenv("TRACELOOP_TRACE_CONTENT", raising=False)
    _Handler.routes = {"/v1/chat/completions": (200, "application/json", CHAT_COMPLETION)}
    provider, exporter = _provider()

    instruments._setup_llm_instruments()  # sets the content default
    instrumentor = OpenAIInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    instrumentor.instrument(tracer_provider=provider)
    try:
        client = openai.OpenAI(api_key="sk-test", base_url=f"{server}/v1")
        client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Extrato: PIX FORNECEDOR X 12.345.678/0001-90"}],
        )
    finally:
        instrumentor.uninstrument()

    [span] = [s for s in exporter.get_finished_spans() if s.name.startswith("openai")]
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.response.model"] == "gpt-4o-2024-08-06"
    assert attrs["gen_ai.usage.input_tokens"] == 1840
    assert attrs["gen_ai.usage.output_tokens"] == 62
    dumped = json.dumps({k: str(v) for k, v in attrs.items()})
    assert "PIX FORNECEDOR" not in dumped
    assert "Conta 3.1.02" not in dumped
