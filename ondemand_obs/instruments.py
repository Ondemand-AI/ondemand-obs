import json
import logging
import os

_BODY_CAPTURE_MAX = 4096

# Request bodies are recorded ONLY for our own API, where the robot's webhooks
# go (step status, records, run report, artifacts): data the portal already
# holds. Every other host is left alone. A robot talks to bank portals,
# government sites and LLM APIs, whose bodies carry logins and prompts, and no
# list of field names can recognise every site's password field ("txtSenha",
# "j_password", a form-encoded login). An allowlist cannot leak the site
# nobody thought of; a denylist can.
_REQUEST_BODY_HOSTS = ("*.ondemand-ai.com.br",)

# Any JSON key CONTAINING one of these (case-insensitive) is replaced by
# "[redacted]" at any depth, in both captured request bodies and error
# response bodies. Substring, not exact match, so access_token, j_password and
# client_secret are caught too.
_SECRET_KEY_PARTS = (
    "password", "passwd", "senha", "secret", "token",
    "api_key", "apikey", "authorization", "credential",
)

_REDACTED = "[redacted]"

# Error responses go out as log records on this logger, wired straight to the
# OTLP handler and NOT propagated to the root logger: the root also feeds the
# portal's live console, and a third party's error body belongs in HyperDX,
# not in front of the client.
_error_logger = logging.getLogger("ondemand_obs.http_errors")


def _host_matches(host, patterns):
    host = (host or "").lower().rstrip(".")
    for pattern in patterns:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def _is_secret_key(key):
    k = str(key).lower()
    return any(part in k for part in _SECRET_KEY_PARTS)


def _redact(value):
    if isinstance(value, dict):
        return {k: (_REDACTED if _is_secret_key(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _redacted_json(raw):
    """Parse JSON bytes and return them redacted and truncated, or None if the
    body is not JSON. Redaction needs structure: a body we cannot parse is a
    body we cannot vouch for."""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return json.dumps(_redact(parsed), ensure_ascii=False)[:_BODY_CAPTURE_MAX]


def _request_body(request):
    """Raw bytes of an httpx request body, or None for a streaming body we must
    not consume. The instrumentor hands us a RequestInfo whose `stream` is an
    httpx.ByteStream holding the bytes in `_stream` (0.1.8 read `_buffer`,
    which httpx never had, so nothing was ever captured)."""
    stream = getattr(request, "stream", None)
    raw = getattr(stream, "_stream", None)
    return raw if isinstance(raw, bytes) else None


def _httpx_request_hook(span, request):
    try:
        if not _host_matches(request.url.host, _REQUEST_BODY_HOSTS):
            return
        raw = _request_body(request)
        body = _redacted_json(raw) if raw else None
        if body:
            span.set_attribute("http.request.body", body)
    except Exception:
        pass


def _log_error_response(request, response):
    """Record the body of a 4xx/5xx response, where the other side explains
    what went wrong. Any host: error bodies rarely carry credentials, and
    secret-looking keys are redacted anyway. JSON is redacted; plain text is
    truncated; HTML error pages and binary bodies are skipped."""
    try:
        if response.status_code < 400:
            return
        try:
            raw = response.content
        except Exception:
            # Streaming response nobody has read: consuming it here would
            # steal the body from the caller.
            return
        if not raw:
            return
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            body = _redacted_json(raw)
        elif content_type.startswith("text/plain"):
            body = raw.decode("utf-8", errors="replace")[:_BODY_CAPTURE_MAX]
        else:
            body = None
        if not body:
            return
        _error_logger.warning(
            "HTTP %s %s %s%s -> %s: %s",
            response.status_code, request.method, request.url.host, request.url.path,
            response.status_code, body,
            extra={
                "http.response.status_code": response.status_code,
                "http.request.method": request.method,
                "server.address": request.url.host,
                "url.path": request.url.path,
                "http.response.body": body,
            },
        )
    except Exception:
        pass


def _install_error_response_logging():
    """Wrap httpx's public Client.send / AsyncClient.send. The httpx
    instrumentor's response hook fires before the body is read and the span
    ends right after, so the body cannot be a span attribute without poking
    httpx internals. Instead we log it once the client has read it; the log
    record carries the caller's trace context, so HyperDX shows it in the
    same trace."""
    import httpx

    if getattr(httpx.Client.send, "_ondemand_obs", False):
        return

    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def send(self, request, *args, **kwargs):
        response = original_send(self, request, *args, **kwargs)
        _log_error_response(request, response)
        return response

    async def async_send(self, request, *args, **kwargs):
        response = await original_async_send(self, request, *args, **kwargs)
        _log_error_response(request, response)
        return response

    send._ondemand_obs = True
    async_send._ondemand_obs = True
    httpx.Client.send = send
    httpx.AsyncClient.send = async_send


def _setup_llm_instruments():
    """OpenAI / Anthropic SDK calls as gen_ai spans: model, token usage,
    finish reason, tool calls, latency, errors. Only active when the
    [llm] extra and the SDK are installed.

    Prompt and completion TEXT are off: our prompts carry client data. A deploy
    can still turn them on with TRACELOOP_TRACE_CONTENT=true."""
    os.environ.setdefault("TRACELOOP_TRACE_CONTENT", "false")

    for module, cls in (
        ("opentelemetry.instrumentation.openai", "OpenAIInstrumentor"),
        ("opentelemetry.instrumentation.anthropic", "AnthropicInstrumentor"),
    ):
        try:
            instrumentor = getattr(__import__(module, fromlist=[cls]), cls)()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument()
        except Exception:
            # Extra not installed, SDK not installed, or an incompatible
            # version: observability must never stop a robot from starting.
            pass


def setup_instruments(log_handler=None) -> None:
    """Register auto-instrumentation for common HTTP clients and LLM SDKs."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument(request_hook=_httpx_request_hook)
    except ImportError:
        pass

    if log_handler is not None:
        _error_logger.addHandler(log_handler)
        _error_logger.propagate = False
        _error_logger.setLevel(logging.WARNING)
        try:
            _install_error_response_logging()
        except ImportError:
            pass

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except ImportError:
        pass

    _setup_llm_instruments()
