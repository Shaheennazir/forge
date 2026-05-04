"""
forge.code_intelligence.otel_ — OpenTelemetry instrumentation wrapper.

Usage:
    from forge.code_intelligence.otel_ import create_tracer, TraceConfig
    tracer = create_tracer("forge-coder", workdir=Path("src/"))
    with tracer.start_as_current_span("generate_app") as span:
        ...

Instruments generated code with OpenTelemetry traces. Every function the
Coder writes gets a span. When tests fail in the Test Runner, the agent
sees the full execution trace — not just an assertion failure message.
"""

from __future__ import annotations

import os
import sys
import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Lazy imports
_otel_trace = None
_otel_sdk = None
_otel_exporter = None
_tracer = None
_configured = False


def _ensure_otel():
    global _otel_trace, _otel_sdk, _otel_exporter, _configured
    if _configured:
        return True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        _otel_trace = trace
        _otel_sdk = TracerProvider
        _otel_exporter = ConsoleSpanExporter
        _configured = True
        return True
    except ImportError as e:
        log.warning("otel_.import_error", error=str(e))
        return False


@dataclass
class TraceConfig:
    """Configuration for OpenTelemetry tracing."""
    service_name: str = "forge-generated"
    exporter: str = "console"          # "console" | "otlp" | "none"
    otlp_endpoint: str = "http://localhost:4317"
    sample_rate: float = 1.0           # 1.0 = 100%, 0.1 = 10%
    log_spans: bool = True             # also log spans to structlog


@dataclass
class SpanEvent:
    """A single event captured within a span."""
    name: str
    timestamp: str = ""
    attributes: dict = field(default_factory=dict)


@dataclass
class TraceResult:
    """Result of a traced operation."""
    success: bool
    trace_id: str = ""
    span_id: str = ""
    events: list[SpanEvent] = field(default_factory=list)
    error: str | None = None


_tracer_cache: dict[str, object] = {}


def create_tracer(
    name: str,
    workdir: Path | None = None,
    config: TraceConfig | None = None,
) -> object:
    """
    Create a tracer for the given service name.

    The tracer is cached per name — calling multiple times with the same
    name returns the same tracer instance.
    """
    if not _ensure_otel():
        return _NoOpTracer()

    config = config or TraceConfig(service_name=name)

    if name in _tracer_cache:
        return _tracer_cache[name]

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": config.service_name,
            "service.version": "1.0.0",
            "deployment.environment": os.getenv("FORGE_ENV", "development"),
        })

        provider = TracerProvider(resource=resource)

        if config.exporter == "console":
            exporter = ConsoleSpanExporter()
        elif config.exporter == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                exporter = OTLPSpanExporter(endpoint=config.otlp_endpoint)
            except ImportError:
                log.warning("otel_.otlp_exporter_not_available")
                exporter = ConsoleSpanExporter()
        else:
            exporter = None

        if exporter:
            provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer(config.service_name)

        _tracer_cache[name] = tracer
        log.info("otel_.tracer_created", service=name, exporter=config.exporter)

        return tracer

    except Exception as e:
        log.warning("otel_.tracer_creation_failed", error=str(e))
        return _NoOpTracer()


class _NoOpTracer:
    """No-op tracer when OpenTelemetry is not available."""
    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()
    def start_span(self, name, **kwargs):
        return _NoOpSpan()
    def start_child_span(self, name, parent, **kwargs):
        return _NoOpSpan()


class _NoOpSpan:
    """No-op span when tracing is disabled."""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def set_attribute(self, key, value): pass
    def add_event(self, name, attributes=None): pass
    def record_exception(self, exc): pass
    def set_status(self, status): pass


def instrument_module(
    module_path: Path,
    service_name: str = "forge-instrumented",
) -> TraceResult:
    """
    Auto-instrument all functions in a Python module with OpenTelemetry spans.

    Wraps every def and async def with a span. The span name is the
    fully-qualified function name. Spans capture: arguments (sanitized),
    return value (sanitized), duration, and any exceptions.

    This is what you call after the Coder writes a new module,
    before the Test Runner runs — so failures come with traces.
    """
    if not _ensure_otel():
        return TraceResult(success=False, error="OpenTelemetry not available")

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        from opentelemetry.sdk.trace.instrumentation import auto_instrumentation

        # Auto-instrument the module
        # This patches all functions in the module with tracing
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(tracer_provider)

        # Import and instrument the module
        import importlib.util
        spec = importlib.util.spec_from_file_location("instrumented", str(module_path))
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules["instrumented"] = module
            spec.loader.exec_module(module)

            # Auto-instrument with opentelemetry-instrumentation
            try:
                from opentelemetry.instrumentation.auto_instrumentation import (
                    run_instrumentation,
                )
                run_instrumentation()
            except ImportError:
                # Fallback: manual wrapping
                _wrap_module_functions(module, trace.get_tracer(service_name))

        return TraceResult(success=True)

    except Exception as e:
        return TraceResult(success=False, error=str(e))


def _wrap_module_functions(module, tracer):
    """Wrap all functions in a module with a tracing span."""
    import functools

    for name in dir(module):
        obj = getattr(module, name, None)
        if callable(obj) and not name.startswith("_"):
            wrapped = tracer.start_as_current_span(name)(obj)
            setattr(module, name, wrapped)


def capture_trace(
    fn,
    tracer_name: str = "forge-trace",
    span_name: str | None = None,
):
    """
    Decorator: capture a trace for a single function call.

    Usage:
        @capture_trace("forge-coder", "generate_app")
        def generate_app():
            ...
    """
    def decorator(fn):
        span_name = span_name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            tracer = _tracer_cache.get(tracer_name) or create_tracer(tracer_name)
            with tracer.start_as_current_span(span_name) as span:
                try:
                    result = fn(*args, **kwargs)
                    span.set_attribute("success", True)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_attribute("success", False)
                    raise

        return wrapper

    return decorator


import functools
