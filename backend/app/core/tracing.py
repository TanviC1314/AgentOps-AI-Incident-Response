"""OpenTelemetry setup. Exports spans via OTLP/HTTP only when an endpoint is configured."""
import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)
_configured = False


def setup_tracing(service_name: str, otlp_endpoint: str = "") -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")))
        except Exception:  # never let tracing break startup
            logger.warning("OTLP exporter unavailable; traces are not exported", exc_info=True)
    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer(name: str = "agentops"):
    return trace.get_tracer(name)
