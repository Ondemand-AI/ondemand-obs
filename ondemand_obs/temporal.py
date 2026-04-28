def get_tracing_interceptor():
    """
    Return a Temporal TracingInterceptor wired to the active OTel tracer provider.
    Requires ondemand-obs[temporal] (temporalio>=1.7).
    """
    try:
        from temporalio.contrib.opentelemetry import TracingInterceptor
    except ImportError as e:
        raise ImportError(
            "temporalio is required for Temporal tracing. "
            "Install ondemand-obs[temporal] or ondemand-ai[worker]."
        ) from e
    return TracingInterceptor()
