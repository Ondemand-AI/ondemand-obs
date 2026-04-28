_BODY_CAPTURE_MAX = 4096


def _httpx_request_hook(span, request):
    try:
        stream = getattr(request, "stream", None)
        if stream is not None and hasattr(stream, "_buffer"):
            body = stream._buffer.decode("utf-8", errors="replace")[:_BODY_CAPTURE_MAX]
            if body:
                span.set_attribute("http.request.body", body)
    except Exception:
        pass


def setup_instruments() -> None:
    """Register auto-instrumentation for common HTTP clients."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument(
            request_hook=_httpx_request_hook,
        )
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except ImportError:
        pass
