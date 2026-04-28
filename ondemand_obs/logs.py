import logging

from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

# LoggingHandler bridges Python's logging module → OTel log records → OTLP exporter
try:
    from opentelemetry.sdk._logs import LoggingHandler
except ImportError:
    # opentelemetry-sdk >= 1.28 may move this; fall back gracefully
    from opentelemetry.sdk.logs import LoggingHandler  # type: ignore[no-redef]


def setup_logs(endpoint: str, headers: dict, resource: Resource) -> tuple:
    exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers)
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(provider)
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    return provider, handler
