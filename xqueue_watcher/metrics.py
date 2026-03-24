"""
OpenTelemetry metrics for xqueue-watcher.

Call :func:`configure_metrics` once at process startup (before the first
submission is processed).  All configuration is read from the standard
OpenTelemetry environment variables so no application-level config files are
needed:

``OTEL_EXPORTER_OTLP_ENDPOINT``
    OTLP collector endpoint, e.g. ``http://otel-collector:4318``.
    When absent or empty, metrics are recorded in-process but not exported.
``OTEL_SERVICE_NAME``
    Service name attached to every metric (default: ``xqueue-watcher``).
``OTEL_RESOURCE_ATTRIBUTES``
    Additional resource attributes as ``key=value,...`` pairs.  Parsed
    automatically by the OpenTelemetry SDK's ``Resource.create()`` call —
    no custom parsing is needed in this module.
"""

import os

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

_METER_NAME = "xqueue_watcher"
_DEFAULT_SERVICE_NAME = "xqueue-watcher"


def _build_meter_provider() -> MeterProvider:
    resource = Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", "").strip() or _DEFAULT_SERVICE_NAME}
    )
    readers = []
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
    return MeterProvider(resource=resource, metric_readers=readers)


def configure_metrics() -> None:
    """Configure the global OTel MeterProvider from environment variables."""
    metrics.set_meter_provider(_build_meter_provider())


# ---------------------------------------------------------------------------
# Instruments
#
# Created at module level against the global proxy meter.  The OTel proxy
# delegates transparently to whichever MeterProvider is active, so these
# instruments work correctly whether configure_metrics() has been called or
# not (unmeasured data simply goes to the no-op provider until the real
# provider is installed).
# ---------------------------------------------------------------------------

_meter = metrics.get_meter(_METER_NAME)

process_item_counter = _meter.create_counter(
    "xqueuewatcher.process_item",
    description="Number of grading submissions received.",
)

grader_payload_error_counter = _meter.create_counter(
    "xqueuewatcher.grader_payload_error",
    description="Number of submissions whose grader_payload could not be parsed.",
)

grading_time_histogram = _meter.create_histogram(
    "xqueuewatcher.grading_time",
    unit="s",
    description="Wall-clock time in seconds spent grading a single submission.",
)

replies_counter = _meter.create_counter(
    "xqueuewatcher.replies",
    description="Number of successful (non-exception) grading replies sent.",
)
