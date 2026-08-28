from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from .workflow_context import TemporalIdSpanProcessor


def setup_traces(endpoint: str, headers: dict, resource: Resource) -> TracerProvider:
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)
    provider = TracerProvider(resource=resource)
    # Registered BEFORE the exporting processor: on_start runs in registration
    # order, so the attributes are present by the time the span is batched.
    provider.add_span_processor(TemporalIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider
