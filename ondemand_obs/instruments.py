_BODY_CAPTURE_MAX = 4096


def _httpx_request_hook(span, request):
    if request.content:
        body = request.content.decode("utf-8", errors="replace")[:_BODY_CAPTURE_MAX]
        span.set_attribute("http.request.body", body)


def _httpx_response_hook(span, request, response):
    if hasattr(response, "text"):
        span.set_attribute("http.response.body", response.text[:_BODY_CAPTURE_MAX])
    elif hasattr(response, "content"):
        span.set_attribute("http.response.body",
                           response.content.decode("utf-8", errors="replace")[:_BODY_CAPTURE_MAX])


def setup_instruments() -> None:
    """Register auto-instrumentation for common HTTP clients."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument(
            request_hook=_httpx_request_hook,
            response_hook=_httpx_response_hook,
        )
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except ImportError:
        pass
