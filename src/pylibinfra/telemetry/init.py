"""Bootstrap OTel SDK: tracer/meter/logger providers, OTLP HTTP-экспортёры
(с Bearer-токеном для корп ingest) всех трёх сигналов.

Выставляет resource-атрибуты service.name, service.version,
deployment.environment.name из Config; tenant/service.namespace — только если
Config.tenant задан (типично local-dev), иначе их force-сетит корп
otel-collector. Включает system-метрики через SystemMetricsInstrumentor.

Метрики и трейсы идут только в OTLP — в stdout их нет; stdout-копию логов даёт
logkit.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .config import Config


@dataclass
class Providers:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider

    def shutdown(self) -> None:
        self.logger_provider.shutdown()
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()


def init(cfg: Config) -> Providers:
    res = _resource(cfg)
    providers = _init_otlp(cfg, res)

    trace.set_tracer_provider(providers.tracer_provider)
    metrics.set_meter_provider(providers.meter_provider)
    set_logger_provider(providers.logger_provider)
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # system_* метрики (cpu/mem/gc/runtime): без них не отличить memory leak от
    # utilization spike.
    SystemMetricsInstrumentor().instrument(meter_provider=providers.meter_provider)

    return providers


def _resource(cfg: Config) -> Resource:
    attrs: dict[str, str] = {
        "service.name": cfg.service_name,
        "service.version": cfg.service_version,
        "deployment.environment.name": cfg.environment,
    }
    if cfg.tenant:
        attrs["service.namespace"] = cfg.tenant
        attrs["tenant"] = cfg.tenant
    return Resource.create(attrs)


def _otlp_url(cfg: Config, signal_path: str) -> str:
    scheme = "http" if cfg.otlp_insecure else "https"
    return f"{scheme}://{cfg.otlp_endpoint}/{signal_path}"


def _otlp_headers(cfg: Config) -> dict[str, str] | None:
    if not cfg.otlp_bearer_token:
        return None
    return {"Authorization": f"Bearer {cfg.otlp_bearer_token}"}


def _init_otlp(cfg: Config, res: Resource) -> Providers:
    headers = _otlp_headers(cfg)
    timeout_ms = int(cfg.export_timeout * 1000)

    tp = TracerProvider(
        resource=res,
        sampler=ParentBased(TraceIdRatioBased(cfg.sample_ratio)),
    )
    tp.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=_otlp_url(cfg, "v1/traces"), headers=headers),
            schedule_delay_millis=timeout_ms,
        )
    )

    mp = MeterProvider(
        resource=res,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=_otlp_url(cfg, "v1/metrics"), headers=headers),
                export_interval_millis=timeout_ms,
            )
        ],
    )

    lp = LoggerProvider(resource=res)
    lp.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=_otlp_url(cfg, "v1/logs"), headers=headers)
        )
    )

    return Providers(tracer_provider=tp, meter_provider=mp, logger_provider=lp)
