import pytest

from pylibinfra.timerkit import Job, JobError, Jobs, validate


async def _noop() -> None:
    return None


def test_job_defaults() -> None:
    job = Job(name="x", interval=1.0, run=_noop)

    assert (job.timeout, job.jitter, job.run_on_start) == (0.0, 0.0, False)
    jobs: Jobs = (job,)
    assert jobs[0].name == "x"


def test_valid_set_passes() -> None:
    validate((Job(name="a", interval=1, run=_noop), Job(name="b", interval=2, run=_noop)))
    validate(())


def test_empty_name_rejected() -> None:
    with pytest.raises(JobError, match="name is empty"):
        validate((Job(name="", interval=1, run=_noop),))


def test_missing_run_rejected() -> None:
    with pytest.raises(JobError, match="run is none"):
        validate((Job(name="a", interval=1, run=None),))  # type: ignore[arg-type]


@pytest.mark.parametrize("interval", [0, -1.5])
def test_non_positive_interval_rejected(interval: float) -> None:
    with pytest.raises(JobError, match="interval must be > 0"):
        validate((Job(name="a", interval=interval, run=_noop),))


@pytest.mark.parametrize(("timeout", "jitter"), [(-1, 0), (0, -1)])
def test_negative_timeout_or_jitter_rejected(timeout: float, jitter: float) -> None:
    with pytest.raises(JobError, match="timeout and jitter must be >= 0"):
        validate((Job(name="a", interval=1, run=_noop, timeout=timeout, jitter=jitter),))


def test_duplicate_name_rejected() -> None:
    job = Job(name="a", interval=1, run=_noop)

    with pytest.raises(JobError, match="duplicate job name 'a'"):
        validate((job, job))


def test_job_error_is_value_error() -> None:
    assert issubclass(JobError, ValueError)


def test_job_is_frozen() -> None:
    job = Job(name="a", interval=1, run=_noop)

    with pytest.raises(AttributeError):
        job.name = "b"  # type: ignore[misc]
