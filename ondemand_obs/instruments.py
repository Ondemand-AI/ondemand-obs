import json
import logging
import os
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

_BODY_CAPTURE_MAX = 65536

# Request bodies are recorded for EVERY host: our API, bank portals, government
# sites, LLM APIs. When a client's API rejects a call, what we sent is the first
# thing anyone asks. What must never reach HyperDX is OUR credentials, and those
# are removed by value (see register_secret), so a password is caught whatever
# the site calls its field.
#
# The one exception is the Bitwarden CLI server on localhost: its /unlock call
# carries the vault's master password and its item responses carry every
# credential we hold. Nothing from loopback is recorded.
_NEVER_CAPTURE_HOSTS = ("localhost", "127.0.0.1", "::1")

# Any JSON or form key CONTAINING one of these (case-insensitive) is replaced by
# "[redacted]" at any depth. Substring, not exact match, so access_token,
# j_password and client_secret are caught too. This covers tokens a site hands
# back to us, which no vault knows about.
_SECRET_KEY_PARTS = (
    "password", "passwd", "senha", "secret", "token",
    "api_key", "apikey", "authorization", "credential",
)

_REDACTED = "[redacted]"

# Our own credentials, by value: env vars whose name looks secret (read at
# setup) plus whatever the SDK registers as it fetches vault items. Every
# recorded body is scrubbed of each of them, raw and in the encodings a body
# carries them in. Shorter values are skipped: scrubbing "1234" from every
# body would destroy data and protect nothing.
_MIN_SECRET_LEN = 6
_secret_values = set()

# Error responses go out as log records on this logger, wired straight to the
# OTLP handler and NOT propagated to the root logger: the root also feeds the
# portal's live console, and a third party's error body belongs in HyperDX,
# not in front of the client.
_error_logger = logging.getLogger("ondemand_obs.http_errors")


def register_secret(value):
    """Never record `value` in any captured body. Safe to call before or
    without configure_observability()."""
    if not isinstance(value, str) or len(value) < _MIN_SECRET_LEN:
        return
    _secret_values.update({
        value,
        quote(value, safe=""),
        quote_plus(value),
        json.dumps(value, ensure_ascii=False)[1:-1],
        json.dumps(value)[1:-1],
    })


def _register_env_secrets():
    for name, value in os.environ.items():
        if _is_secret_key(name):
            register_secret(value)


def _scrub(text):
    # Longest first, so a value's encoded form is not half-replaced by a
    # shorter secret it happens to contain.
    for value in sorted(_secret_values, key=len, reverse=True):
        if value in text:
            text = text.replace(value, _REDACTED)
    return text


def _is_secret_key(key):
    k = str(key).lower()
    return any(part in k for part in _SECRET_KEY_PARTS)


def _redact(value):
    if isinstance(value, dict):
        return {k: (_REDACTED if _is_secret_key(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _redact_form(text):
    pairs = parse_qsl(text, keep_blank_values=True)
    return "&".join(
        f"{quote_plus(k)}={_REDACTED if _is_secret_key(k) else quote_plus(v)}"
        for k, v in pairs
    )


def _format_body(raw, content_type):
    """The text of a body as it will be recorded: secret-looking JSON and form
    keys redacted, our own secrets scrubbed by value, then truncated. None for
    multipart and binary bodies, which are files, not something to read."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or not raw:
        return None
    ctype = (content_type or "").lower()
    if ctype.startswith("multipart/") or "octet-stream" in ctype:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        text = json.dumps(_redact(json.loads(text)), ensure_ascii=False)
    except ValueError:
        if "x-www-form-urlencoded" in ctype:
            text = _redact_form(text)
    # Scrub before truncating: a cut could split a secret and hide it from us.
    return _scrub(text)[:_BODY_CAPTURE_MAX]


def _host_is_excluded(host):
    host = (host or "").lower().rstrip(".").strip("[]")
    return host in _NEVER_CAPTURE_HOSTS


def _record_request_body(span, host, raw, content_type):
    if _host_is_excluded(host) or raw is None:
        return
    if isinstance(raw, (bytes, str)):
        span.set_attribute("http.request.body.size", len(raw))
    body = _format_body(raw, content_type)
    if body:
        span.set_attribute("http.request.body", body)


def _httpx_body(request):
    """Raw bytes of an httpx request body, or None for a streaming body we must
    not consume. The instrumentor hands us a RequestInfo whose `stream` is an
    httpx.ByteStream holding the bytes in `_stream` (0.1.8 read `_buffer`,
    which httpx never had, so nothing was ever captured)."""
    stream = getattr(request, "stream", None)
    raw = getattr(stream, "_stream", None)
    return raw if isinstance(raw, bytes) else None


def _httpx_request_hook(span, request):
    try:
        _record_request_body(
            span, request.url.host, _httpx_body(request),
            request.headers.get("content-type"),
        )
    except Exception:
        pass


def _requests_body(request):
    """A requests PreparedRequest body: bytes or str, or a generator/file for
    a streaming upload, which we must not consume."""
    body = getattr(request, "body", None)
    return body if isinstance(body, (bytes, str)) else None


def _requests_request_hook(span, request):
    try:
        _record_request_body(
            span, urlsplit(request.url).hostname, _requests_body(request),
            request.headers.get("content-type"),
        )
    except Exception:
        pass


def _log_error(method, host, path, status, response_headers, raw, request_body,
               request_content_type):
    """Record the body of a 4xx/5xx response, where the other side explains
    what went wrong, next to the request body that provoked it. JSON is
    redacted, text/plain kept, HTML error pages and binary bodies skipped."""
    if status < 400 or _host_is_excluded(host) or not raw:
        return
    content_type = response_headers.get("content-type", "").lower()
    if "json" in content_type or content_type.startswith("text/plain"):
        body = _format_body(raw, content_type)
    else:
        body = None
    if not body:
        return
    extra = {
        "http.response.status_code": status,
        "http.request.method": method,
        "server.address": host,
        "url.path": path,
        "http.response.body": body,
    }
    sent = _format_body(request_body, request_content_type) if request_body else None
    if sent:
        extra["http.request.body"] = sent
    _error_logger.warning(
        "HTTP %s %s %s%s -> %s: %s", status, method, host, path, status, body,
        extra=extra,
    )


def _log_error_response(request, response):
    """httpx: log an error response. A streaming response nobody has read is
    skipped: consuming it here would steal the body from the caller."""
    try:
        if response.status_code < 400:
            return
        try:
            raw = response.content
        except Exception:
            return
        try:
            sent = request.content
        except Exception:
            sent = None
        _log_error(
            request.method, request.url.host, request.url.path,
            response.status_code, response.headers, raw,
            sent, request.headers.get("content-type"),
        )
    except Exception:
        pass


def _requests_response_hook(span, request, response):
    """requests: log an error response. With stream=True the body is still
    unread (`_content is False`) and belongs to the caller."""
    try:
        if response.status_code < 400 or getattr(response, "_content", False) is False:
            return
        url = urlsplit(request.url)
        _log_error(
            request.method, url.hostname, url.path,
            response.status_code, response.headers, response.content,
            _requests_body(request), request.headers.get("content-type"),
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
    send.__wrapped__ = original_send
    async_send.__wrapped__ = original_async_send
    httpx.Client.send = send
    httpx.AsyncClient.send = async_send


def _setup_llm_instruments():
    """OpenAI / Anthropic SDK calls as gen_ai spans: model, token usage,
    finish reason, tool calls, latency, errors. Only active when the
    [llm] extra and the SDK are installed.

    The gen_ai span leaves prompt and completion TEXT off. The prompt is still
    recorded, as the request body of the httpx span underneath it. A deploy
    can put the text on the gen_ai span too with TRACELOOP_TRACE_CONTENT=true."""
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
    _register_env_secrets()

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
        RequestsInstrumentor().instrument(
            request_hook=_requests_request_hook,
            response_hook=_requests_response_hook if log_handler is not None else None,
        )
    except ImportError:
        pass

    _setup_llm_instruments()
