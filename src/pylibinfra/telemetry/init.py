"""Bootstrap OTel SDK: провайдеры трёх сигналов, OTLP/HTTP-экспорт, propagator, system-метрики."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field

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
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from pylibinfra.logkit import Logger

from .config import Config, ConfigError, signal_url
from .errorlog import install_error_logging

# Бюджет одного шага shutdown'а, когда Providers собраны вручную и таймаут неизвестен.
DEFAULT_EXPORT_TIMEOUT = 10.0

# Минимум времени шагу, когда общий дедлайн уже съеден предыдущими: нулевой бюджет
# гарантированно роняет шаг и выбрасывает его очередь, ничего не отправив.
MIN_STEP_TIMEOUT = 0.1


@dataclass
class Providers:
    """Поднятые SDK-провайдеры трёх сигналов; `shutdown` гасит их с общим дедлайном."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    export_timeout: float = field(default=DEFAULT_EXPORT_TIMEOUT)

    def shutdown(self, timeout: float | None = None) -> None:
        """Гасит tracer → meter → logger, деля `timeout` между шагами.

        Логи последними: ошибки остановки других сигналов ещё успевают в них
        попасть. Каждому шагу достаётся `min(export_timeout, остаток / число
        оставшихся)`, но не меньше `MIN_STEP_TIMEOUT`: шаг, переживший свой
        бюджет, не обнуляет следующие. Остановка не прерывается; сбои шагов
        собираются в `ExceptionGroup`.
        """
        steps: tuple[tuple[str, Callable[[float], None]], ...] = (
            ("tracer provider", self._stop_tracer),
            ("meter provider", self._stop_meter),
            ("logger provider", self._stop_logger),
        )
        deadline = None if timeout is None else time.monotonic() + timeout
        errors: list[Exception] = []
        for index, (name, stop) in enumerate(steps):
            try:
                stop(self._step_budget(deadline, len(steps) - index))
            except Exception as exc:
                errors.append(RuntimeError(f"telemetry: shutdown: {name}: {exc}"))
        if errors:
            raise ExceptionGroup("telemetry: shutdown", errors)

    def _step_budget(self, deadline: float | None, remaining: int) -> float:
        budget = self.export_timeout if self.export_timeout > 0 else DEFAULT_EXPORT_TIMEOUT
        if deadline is None:
            return budget
        share = max(deadline - time.monotonic(), 0.0) / remaining
        return min(budget, max(share, MIN_STEP_TIMEOUT))

    def _stop_tracer(self, budget: float) -> None:
        self.tracer_provider.force_flush(int(budget * 1000))
        self.tracer_provider.shutdown()

    def _stop_meter(self, budget: float) -> None:
        self.meter_provider.shutdown(timeout_millis=budget * 1000)

    def _stop_logger(self, budget: float) -> None:
        self.logger_provider.force_flush(int(budget * 1000))
        self.logger_provider.shutdown()


def init(cfg: Config, *, error_logger: Logger | None = None) -> Providers:
    """Поднимает SDK, выставляет глобальные провайдеры и propagator, отдаёт `Providers`.

    `error_logger` принимает диагностику SDK (ошибки экспорта, внутренние
    сообщения); без него она уходит в stdlib-`logging`, как её пишет SDK.
    При `OTEL_SDK_DISABLED` провайдеры поднимаются без экспортёров: сигналы
    дропаются, сети нет.
    """
    if error_logger is not None:
        install_error_logging(error_logger)

    if cfg.disabled:
        providers = Providers(
            tracer_provider=TracerProvider(sampler=ALWAYS_OFF),
            meter_provider=MeterProvider(),
            logger_provider=LoggerProvider(),
            export_timeout=cfg.export_timeout,
        )
        _set_globals(providers)
        return providers

    resource = _resource(cfg)
    headers = cfg.headers()
    # Провайдер поднимает фоновый поток батчера: сбой на следующем шаге обязан
    # погасить уже собранные, иначе потоки живут до конца процесса.
    with ExitStack() as rollback:
        tracer_provider = _tracer_provider(cfg, resource, headers)
        rollback.callback(tracer_provider.shutdown)
        meter_provider = _meter_provider(cfg, resource, headers)
        rollback.callback(meter_provider.shutdown)
        logger_provider = _logger_provider(cfg, resource, headers)
        rollback.callback(logger_provider.shutdown)

        # Без system-метрик не отличить memory leak от utilization spike.
        instrumentor = SystemMetricsInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(meter_provider=meter_provider)

        providers = Providers(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
            export_timeout=cfg.export_timeout,
        )
        _set_globals(providers)
        rollback.pop_all()
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


def _url(cfg: Config, signal: str) -> str:
    try:
        return signal_url(cfg.otlp_endpoint, signal)
    except ValueError as exc:
        raise ConfigError(f"telemetry: invalid config: {exc}") from None


def _tracer_provider(cfg: Config, resource: Resource, headers: dict[str, str]) -> TracerProvider:
    exporter = OTLPSpanExporter(
        endpoint=_url(cfg, "v1/traces"), headers=headers or None, timeout=cfg.export_timeout
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(cfg.sample_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            schedule_delay_millis=cfg.trace_batch_delay * 1000,
            export_timeout_millis=cfg.export_timeout * 1000,
        )
    )
    return provider


def _meter_provider(cfg: Config, resource: Resource, headers: dict[str, str]) -> MeterProvider:
    exporter = OTLPMetricExporter(
        endpoint=_url(cfg, "v1/metrics"), headers=headers or None, timeout=cfg.export_timeout
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=cfg.metric_interval * 1000,
        export_timeout_millis=cfg.export_timeout * 1000,
    )
    return MeterProvider(resource=resource, metric_readers=[reader])


def _logger_provider(cfg: Config, resource: Resource, headers: dict[str, str]) -> LoggerProvider:
    exporter = OTLPLogExporter(
        endpoint=_url(cfg, "v1/logs"), headers=headers or None, timeout=cfg.export_timeout
    )
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            exporter,
            schedule_delay_millis=cfg.log_batch_delay * 1000,
            export_timeout_millis=cfg.export_timeout * 1000,
        )
    )
    return provider


def _set_globals(providers: Providers) -> None:
    trace.set_tracer_provider(providers.tracer_provider)
    metrics.set_meter_provider(providers.meter_provider)
    set_logger_provider(providers.logger_provider)
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
