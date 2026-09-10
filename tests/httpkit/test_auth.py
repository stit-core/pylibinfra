from collections.abc import Callable

from fastapi import FastAPI, Request

from pylibinfra.httpkit import (
    AuthUnavailableError,
    Config,
    bind_subject,
    forbidden,
    subject,
)
from pylibinfra.testkit.logtest import Capture

from .conftest import client, one


def _app(log: Capture, extract: Callable[[Request], object], **cfg: object) -> FastAPI:
    from pylibinfra.httpkit import build_app

    app = build_app(Config(**cfg), log, auth=extract)  # type: ignore[arg-type]

    @app.get("/me")
    async def me(request: Request) -> dict[str, object]:
        return {"auth": request.state.auth, "subject": subject()}

    return app


async def test_identity_lands_in_request_state(log: Capture) -> None:
    async def extract(request: Request) -> object:
        bind_subject("u-1")
        return {"sub": request.headers.get("X-Who", "")}

    async with client(_app(log, extract)) as c:
        resp = await c.get("/me", headers={"X-Who": "alice"})

    assert resp.status_code == 200
    assert resp.json() == {"auth": {"sub": "alice"}, "subject": "u-1"}


async def test_generic_error_becomes_401(log: Capture) -> None:
    async def extract(request: Request) -> object:
        raise ValueError("bad token: eyJhbGciOi")

    async with client(_app(log, extract)) as c:
        resp = await c.get("/me")

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
    assert "eyJhbGciOi" not in resp.text
    rejected = one(log, "auth rejected")
    assert rejected.level == "debug"
    assert "eyJhbGciOi" in rejected.value("err")


async def test_api_error_keeps_its_status(log: Capture) -> None:
    async def extract(request: Request) -> object:
        raise forbidden("SCOPE_MISSING", "scope missing")

    async with client(_app(log, extract)) as c:
        resp = await c.get("/me")

    assert resp.status_code == 403
    assert resp.json()["code"] == "SCOPE_MISSING"


async def test_unavailable_backend_becomes_503(log: Capture) -> None:
    async def extract(request: Request) -> object:
        try:
            raise ConnectionError("jwks unreachable")
        except ConnectionError as exc:
            raise AuthUnavailableError("httpkit: jwks") from exc

    async with client(_app(log, extract)) as c:
        resp = await c.get("/me")

    assert resp.status_code == 503
    assert resp.json()["code"] == "AUTH_UNAVAILABLE"
    assert one(log, "auth backend unavailable").level == "warn"


async def test_wrapped_unavailable_cause_is_detected(log: Capture) -> None:
    async def extract(request: Request) -> object:
        try:
            raise AuthUnavailableError("httpkit: jwks")
        except AuthUnavailableError as exc:
            raise RuntimeError("verify") from exc

    async with client(_app(log, extract)) as c:
        resp = await c.get("/me")
    assert resp.status_code == 503


async def test_health_and_docs_stay_public(log: Capture) -> None:
    async def extract(request: Request) -> object:
        raise ValueError("no credentials")

    async with client(_app(log, extract)) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/ready")).status_code == 200
        assert (await c.get("/docs")).status_code == 200
        assert (await c.get("/me")).status_code == 401


async def test_error_response_carries_security_headers(log: Capture) -> None:
    async def extract(request: Request) -> object:
        raise ValueError("nope")

    async with client(_app(log, extract)) as c:
        resp = await c.get("/me")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-request-id"]


async def test_extractor_runs_once_with_idempotency(log: Capture) -> None:
    from pylibinfra.httpkit import MemoryStore, build_app

    calls: list[int] = []

    async def extract(request: Request) -> object:
        calls.append(1)
        bind_subject("u-1")
        return {"sub": "u-1"}

    app = build_app(Config(), log, store=MemoryStore(), auth=extract)

    @app.post("/create")
    async def create(request: Request) -> dict[str, object]:
        return {"auth": request.state.auth}

    async with client(app) as c:
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 200
    assert resp.json() == {"auth": {"sub": "u-1"}}
    assert len(calls) == 1
