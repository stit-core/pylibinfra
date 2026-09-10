"""timerkit — периодические job'ы поверх asyncio с lifecycle через dishka.

Каждый запуск открывает root-спан `timerkit.job <name>` и учитывается в
`timer_job_runs_total{job,result}` и `timer_job_duration_seconds{job}`.
"""

from .module import TimerkitProvider
from .timerkit import DEFAULT_STOP_TIMEOUT, Job, JobError, Jobs, Runner, validate

__all__ = [
    "DEFAULT_STOP_TIMEOUT",
    "Job",
    "JobError",
    "Jobs",
    "Runner",
    "TimerkitProvider",
    "validate",
]
