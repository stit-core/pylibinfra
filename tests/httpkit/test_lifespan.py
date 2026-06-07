from fastapi import FastAPI

from pylibinfra.httpkit import dishka_lifespan


class _FakeContainer:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_dishka_lifespan_closes_container_after_yield() -> None:
    container = _FakeContainer()
    lifespan = dishka_lifespan(container)  # type: ignore[arg-type]
    app = FastAPI()

    async with lifespan(app):
        assert container.closed is False
    assert container.closed is True


async def test_dishka_lifespan_closes_container_on_exception() -> None:
    container = _FakeContainer()
    lifespan = dishka_lifespan(container)  # type: ignore[arg-type]
    app = FastAPI()

    with_error = False
    try:
        async with lifespan(app):
            raise RuntimeError("boom")
    except RuntimeError:
        with_error = True

    assert with_error is True
    assert container.closed is True
