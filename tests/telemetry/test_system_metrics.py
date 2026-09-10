from __future__ import annotations

from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

# Перечень § Observability README: тест держит документацию и SDK в согласии.
DOCUMENTED = {
    "system.cpu.time",
    "system.cpu.utilization",
    "system.memory.usage",
    "system.memory.utilization",
    "system.swap.usage",
    "system.swap.utilization",
    "system.disk.io",
    "system.disk.operations",
    "system.disk.time",
    "system.network.io",
    "system.network.packets",
    "system.network.errors",
    "system.network.dropped_packets",
    "system.network.connections",
    "system.thread_count",
    "process.cpu.time",
    "process.cpu.utilization",
    "process.memory.usage",
    "process.memory.virtual",
    "process.thread.count",
    "process.open_file_descriptor.count",
    "process.context_switches",
    "process.disk.io",
    "process.runtime.cpython.memory",
    "process.runtime.cpython.cpu_time",
    "process.runtime.cpython.cpu.utilization",
    "process.runtime.cpython.thread_count",
    "process.runtime.cpython.context_switches",
    "process.runtime.cpython.gc_count",
    "cpython.gc.collections",
    "cpython.gc.collected_objects",
    "cpython.gc.uncollectable_objects",
}

# Метрики, которые psutil отдаёт везде: остальные зависят от ОС и прав процесса.
ALWAYS_PRESENT = {
    "system.cpu.time",
    "system.memory.usage",
    "process.runtime.cpython.memory",
    "process.runtime.cpython.thread_count",
    "cpython.gc.collections",
}


def test_emitted_system_metrics_match_the_readme() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    SystemMetricsInstrumentor().instrument(meter_provider=provider)

    data = reader.get_metrics_data()
    emitted = {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }

    assert emitted >= ALWAYS_PRESENT
    assert emitted <= DOCUMENTED, emitted - DOCUMENTED
