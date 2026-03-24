import unittest
from unittest.mock import patch, MagicMock

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from xqueue_watcher.metrics import (
    _build_meter_provider,
    _METER_NAME,
    _DEFAULT_SERVICE_NAME,
)


class TestBuildMeterProvider(unittest.TestCase):
    def test_returns_meter_provider(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = _build_meter_provider()
        self.assertIsInstance(provider, MeterProvider)

    def test_no_otlp_endpoint_means_no_readers(self):
        env = {"OTEL_EXPORTER_OTLP_ENDPOINT": ""}
        with patch.dict("os.environ", env):
            provider = _build_meter_provider()
        # No PeriodicExportingMetricReader attached → internal reader list is empty.
        self.assertEqual(provider._sdk_config.metric_readers, [])

    def test_otlp_endpoint_adds_reader(self):
        env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318"}
        mock_exporter = MagicMock()
        mock_reader = MagicMock()
        with patch.dict("os.environ", env), \
             patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter",
                   return_value=mock_exporter) as MockExporter, \
             patch("xqueue_watcher.metrics.PeriodicExportingMetricReader",
                   return_value=mock_reader) as MockReader:
            provider = _build_meter_provider()
            MockExporter.assert_called_once()
            MockReader.assert_called_once_with(mock_exporter)
        self.assertIn(mock_reader, provider._sdk_config.metric_readers)

    def test_default_service_name_applied(self):
        # Empty OTEL_SERVICE_NAME should still fall back to the built-in default.
        with patch.dict("os.environ", {"OTEL_SERVICE_NAME": ""}):
            provider = _build_meter_provider()
        attrs = provider._sdk_config.resource.attributes
        self.assertEqual(attrs.get("service.name"), _DEFAULT_SERVICE_NAME)

    def test_custom_service_name_applied(self):
        env = {"OTEL_SERVICE_NAME": "my-grader"}
        with patch.dict("os.environ", env):
            provider = _build_meter_provider()
        attrs = provider._sdk_config.resource.attributes
        self.assertEqual(attrs.get("service.name"), "my-grader")


class TestInstruments(unittest.TestCase):
    """Verify instruments record correctly against an in-memory provider."""

    def setUp(self):
        self.reader = InMemoryMetricReader()
        self.provider = MeterProvider(metric_readers=[self.reader])
        self.meter = self.provider.get_meter(_METER_NAME)

    def _metric_names(self):
        return {m.name for m in self.reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics}

    def test_process_item_counter(self):
        counter = self.meter.create_counter("xqueuewatcher.process_item")
        counter.add(1)
        counter.add(2)
        names = self._metric_names()
        self.assertIn("xqueuewatcher.process_item", names)

    def test_grader_payload_error_counter(self):
        counter = self.meter.create_counter("xqueuewatcher.grader_payload_error")
        counter.add(1)
        names = self._metric_names()
        self.assertIn("xqueuewatcher.grader_payload_error", names)

    def test_grading_time_histogram(self):
        hist = self.meter.create_histogram("xqueuewatcher.grading_time", unit="s")
        hist.record(0.42)
        names = self._metric_names()
        self.assertIn("xqueuewatcher.grading_time", names)

    def test_replies_counter(self):
        counter = self.meter.create_counter("xqueuewatcher.replies")
        counter.add(1)
        names = self._metric_names()
        self.assertIn("xqueuewatcher.replies", names)
