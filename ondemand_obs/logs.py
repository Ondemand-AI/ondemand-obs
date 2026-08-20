import copy
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


# Custom Python levels have no OTel equivalent: the SDK sets severity_text from
# record.levelname verbatim, so SUCCESS (25) exports as level "success". HyperDX
# builds its level filter from the standard OTel severity names, so a custom text
# is searchable (level:success) but never appears in the dropdown.
#
# Map such levels onto the nearest standard level for export, and preserve the
# original meaning as an attribute instead. The portal console and R2 logs are
# unaffected — OndemandLogFormatter does its own level mapping.
_LEVEL_ALIASES = {
    # levelname -> (otel levelname, otel levelno, outcome attribute)
    "SUCCESS": ("INFO", logging.INFO, "success"),
}

_OUTCOME_ATTR = "ondemand.outcome"


class OndemandLoggingHandler(LoggingHandler):
    """LoggingHandler that normalizes Ondemand's custom levels for OTLP export."""

    def emit(self, record: logging.LogRecord) -> None:
        alias = _LEVEL_ALIASES.get(record.levelname)
        if alias:
            level_name, level_no, outcome = alias
            # Copy so the portal/R2 handlers still see the original level,
            # regardless of handler ordering on the root logger.
            record = copy.copy(record)
            record.levelname = level_name
            record.levelno = level_no
            setattr(record, _OUTCOME_ATTR, outcome)
        super().emit(record)


def setup_logs(endpoint: str, headers: dict, resource: Resource) -> tuple:
    exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers)
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(provider)
    handler = OndemandLoggingHandler(level=logging.NOTSET, logger_provider=provider)
    return provider, handler
