#!/usr/bin/env python3
"""Test script to verify OpenTelemetry export is working"""

import os
import time
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource

# Configure with console exporter to see spans immediately
resource = Resource.create({"service.name": "test-service"})
provider = TracerProvider(resource=resource)

# Add console exporter để see spans ngay lập tức
console_exporter = ConsoleSpanExporter()
provider.add_span_processor(
    BatchSpanProcessor(
        console_exporter, schedule_delay_millis=1000, max_export_batch_size=1
    )
)

# Add OTLP exporter
otlp_endpoint = os.getenv(
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://otel-collector:4318/v1/traces"
)
print(f"Exporting to: {otlp_endpoint}")
otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
provider.add_span_processor(
    BatchSpanProcessor(
        otlp_exporter, schedule_delay_millis=1000, max_export_batch_size=1
    )
)

trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

print("Creating test span...")
with tracer.start_as_current_span("test-span") as span:
    span.set_attribute("test.attribute", "test-value")
    print("Span created")
    time.sleep(0.5)

print("Span ended, waiting for export...")
time.sleep(3)  # Wait for batch processor to export

print("Forcing shutdown to flush all pending spans...")
trace.get_tracer_provider().shutdown()
print("Test complete!")
