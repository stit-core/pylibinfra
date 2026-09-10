"""Дедупликация write-запросов по `Idempotency-Key`: конфиг, контракт store и middleware."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode

from pydantic import Field, model_validator
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings
from pylibinfra.clockkit import Clock, Real
from pylibinfra.logkit import Logger, nop

from .auth import AUTH_DONE_KEY, AuthExtractor, bind_subject, subject, unbind_subject
from .error import error, unavailable, write_error
from .metrics import Instruments, instruments
from .response import ResponseTap, header, tap

HEADER_IDEMPOTENT_REPLAYED = "Idempotent-Replayed"

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH"})

REPLAY_HEADERS = frozenset(
    {
        "content-type",
        "location",
        "etag",
        "content-language",
        "content-disposition",
        "cache-control",
    }
)

SWEEP_EVERY = 60.0


class IdempotencyConfig(KitSettings):
    """Параметры дедупликации write-запросов по ключу клиента."""

    model_config = configkit.settings_config()

    header: str = Field(default="Idempotency-Key", validation_alias="HTTP_IDEMPOTENCY_HEADER")
    ttl: float = Field(default=86400.0, validation_alias="HTTP_IDEMPOTENCY_TTL")
    reserve_ttl: float = Field(default=60.0, validation_alias="HTTP_IDEMPOTENCY_RESERVE_TTL")
    max_body_bytes: int = Field(default=65536, validation_alias="HTTP_IDEMPOTENCY_MAX_BODY_BYTES")
    store_timeout: float = Field(default=2.0, validation_alias="HTTP_IDEMPOTENCY_STORE_TIMEOUT")
    fail_closed: bool = Field(default=False, validation_alias="HTTP_IDEMPOTENCY_FAIL_CLOSED")

    @model_validator(mode="after")
    def _check(self) -> IdempotencyConfig:
        if not self.header:
            raise ValueError("header must not be empty")
        if self.ttl <= 0 or self.reserve_ttl <= 0 or self.store_timeout <= 0:
            raise ValueError("ttl, reserve_ttl and store_timeout must be > 0")
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be > 0")
        return self


@runtime_checkable
class IdempotencyStore(Protocol):
    """Байтовый store ключей идемпотентности; запись сериализует httpkit.

    Реализация обязана: `reserve` — атомарно занять свободный ключ и вернуть токен владельца
    (`ok=False`, если ключ занят резервом или записью); `get` — не отдавать резерв как запись;
    `release` — снять резерв, только пока ключ держит этот токен, не трогая сохранённый ответ.
    Любое исключение httpkit трактует как «store недоступен».
    """

    async def reserve(self, key: str, ttl: float) -> tuple[str, bool]:
        """Занимает свободный ключ на `ttl` и возвращает `(token, ok)`; занятый — `("", False)`."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Отдаёт сохранённую запись; ключ, занятый резервом, записью не считается."""
        ...

    async def set(self, key: str, record: bytes, ttl: float) -> None:
        """Сохраняет запись поверх резерва на `ttl`."""
        ...

    async def release(self, key: str, token: str) -> None:
        """Снимает резерв, только пока ключ держит этот токен; запись не трогает."""
        ...


class StoreUnavailableError(Exception):
    """Внутренний сигнал: операция store не удалась или не уложилась в `store_timeout`."""


@dataclass(slots=True)
class _Entry:
    expires_at: datetime
    reserved: bool = False
    token: str = ""
    record: bytes = b""


class MemoryStore:
    """In-memory `IdempotencyStore` для тестов и одиночного процесса.

    Истёкшие записи выметаются амортизированно: ключ идемпотентности уникален, к истёкшей
    записи повторно не обращаются, и без прохода по словарю она жила бы вечно.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        """`clock` подменяется в тестах, чтобы двигать TTL без реального ожидания."""
        self._clock = clock if clock is not None else Real()
        self._entries: dict[str, _Entry] = {}
        self._last_sweep = self._clock.now()

    async def reserve(self, key: str, ttl: float) -> tuple[str, bool]:
        """Занимает свободный ключ и возвращает токен владельца резерва."""
        self._sweep()
        if self._live(key) is not None:
            return "", False
        token = secrets.token_hex(16)
        self._entries[key] = _Entry(
            expires_at=self._clock.now() + timedelta(seconds=ttl), reserved=True, token=token
        )
        return token, True

    async def get(self, key: str) -> bytes | None:
        """Отдаёт сохранённую запись; ключ, занятый резервом, записью не считается."""
        entry = self._live(key)
        if entry is None or entry.reserved:
            return None
        return entry.record

    async def set(self, key: str, record: bytes, ttl: float) -> None:
        """Сохраняет запись поверх резерва на `ttl`."""
        self._sweep()
        self._entries[key] = _Entry(
            expires_at=self._clock.now() + timedelta(seconds=ttl), record=record
        )

    async def release(self, key: str, token: str) -> None:
        """Снимает резерв владельца токена; чужой резерв и сохранённый ответ не трогает."""
        entry = self._entries.get(key)
        if entry is not None and entry.reserved and entry.token == token:
            del self._entries[key]

    def size(self) -> int:
        """Число живых записей — наблюдаемость вытеснения в тестах."""
        return len(self._entries)

    def _live(self, key: str) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock.now() >= entry.expires_at:
            del self._entries[key]
            return None
        return entry

    def _sweep(self) -> None:
        now = self._clock.now()
        if (now - self._last_sweep).total_seconds() < SWEEP_EVERY:
            return
        self._entries = {k: e for k, e in self._entries.items() if now < e.expires_at}
        self._last_sweep = now


def fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    """Отпечаток запроса: метод, путь, канонизированный query и тело.

    `?dry_run=true` — другой запрос: отдавать ему сохранённый ответ нельзя.
    """
    canonical = urlencode(sorted(parse_qsl(query, keep_blank_values=True), key=lambda kv: kv[0]))
    digest = hashlib.sha256()
    for part in (method, path, canonical):
        digest.update(part.encode())
        digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


def scoped_key(scope: Scope, key: str) -> str:
    """Привязывает ключ к клиенту: субъект устойчивее сырых credentials при ротации токена."""
    digest = hashlib.sha256()
    if sub := subject():
        digest.update(b"sub\0")
        digest.update(sub.encode())
    else:
        digest.update(b"cred\0")
        digest.update(header(scope, "Authorization").encode())
        digest.update(b"\0")
        digest.update(header(scope, "Cookie").encode())
    digest.update(b"\0")
    digest.update(key.encode())
    return digest.hexdigest()


def _discard(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def _guarded[T](coro: Coroutine[Any, Any, T], seconds: float) -> T:
    """Операция store под таймаутом и вне отмены клиента: отвалившийся клиент не рвёт запись.

    Отмена вызывающего до операции не доходит (`shield`), а вот собственный дедлайн её снимает:
    зависший store иначе оставил бы задачу висеть до конца жизни процесса.
    """
    task = asyncio.ensure_future(coro)
    try:
        async with asyncio.timeout(seconds):
            return await asyncio.shield(task)
    except TimeoutError:
        task.cancel()
        raise
    finally:
        if not task.done():
            task.add_done_callback(_discard)


class _Capture:
    """Буфер тела ответа до лимита; переполнение отменяет сохранение записи."""

    __slots__ = ("_send", "buf", "limit", "overflow")

    def __init__(self, send: Send, limit: int) -> None:
        self._send = send
        self.limit = limit
        self.buf = bytearray()
        self.overflow = False

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.body" and not self.overflow:
            body = message.get("body", b"") or b""
            if len(self.buf) + len(body) > self.limit:
                self.overflow = True
                self.buf.clear()
            else:
                self.buf.extend(body)
        await self._send(message)


class IdempotencyMiddleware:
    """Воспроизводит ответ на повтор POST/PUT/PATCH с тем же `Idempotency-Key`.

    Ключ scope'ится субъектом (или `Authorization` + `Cookie`); повтор с другим методом, путём,
    query или телом — 422 `IDEMPOTENCY_KEY_REUSED`, параллельный повтор — 409
    `IDEMPOTENCY_IN_PROGRESS`. Ответы `5xx` и превысившие `max_body_bytes` не сохраняются;
    ошибка store — fail-open, при `fail_closed` — 503 `IDEMPOTENCY_UNAVAILABLE`.
    """

    def __init__(
        self,
        app: ASGIApp,
        cfg: IdempotencyConfig,
        store: IdempotencyStore,
        instr: Instruments | None = None,
        extractor: AuthExtractor | None = None,
        log: Logger | None = None,
    ) -> None:
        """`extractor` включает replay только после успешной аутентификации."""
        self._app = app
        self._cfg = cfg
        self._store = store
        self._instr = instr if instr is not None else instruments()
        self._extractor = extractor
        self._log = log if log is not None else nop()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Запрос без ключа или не-мутирующий проходит насквозь."""
        if scope["type"] != "http" or scope.get("method") not in MUTATING_METHODS:
            await self._app(scope, receive, send)
            return
        key = header(scope, self._cfg.header)
        if not key:
            await self._app(scope, receive, send)
            return

        # Точка сброса для `bind_subject` extractor'а; текущее значение сохраняем —
        # субъект мог положить middleware консьюмера выше по цепочке.
        token = bind_subject(subject())
        try:
            await self._handle(scope, receive, send, key)
        finally:
            unbind_subject(token)

    async def _handle(self, scope: Scope, receive: Receive, send: Send, key: str) -> None:
        observed = tap(scope, send)
        if not await self._authenticate(scope, receive):
            self._count("skipped")
            await self._app(scope, receive, observed.send)
            return

        body, too_big, consumed = await _buffer_body(receive, self._cfg.max_body_bytes)
        replay_receive = _replay_receive(consumed, receive)
        if too_big:
            self._count("skipped")
            await self._app(scope, replay_receive, observed.send)
            return

        fp = fingerprint(
            str(scope.get("method", "")), str(scope.get("path", "")), _query(scope), body
        )
        skey = scoped_key(scope, key)

        try:
            record = await self._load(skey)
            if record is not None:
                await self._replay(observed.send, record, fp)
                return
            token = await self._reserve(skey)
        except StoreUnavailableError:
            self._count("error")
            await self._degrade(scope, replay_receive, observed)
            return

        if token is None:
            await self._in_progress(observed, skey, fp)
            return
        await self._run(scope, replay_receive, observed, skey, token, fp)

    async def _authenticate(self, scope: Scope, receive: Receive) -> bool:
        """Анонимный запрос идёт без дедупликации: чужая сессия не должна получить чужой ответ."""
        if self._extractor is None:
            return True
        try:
            identity = await self._extractor(Request(scope, receive))
        except Exception as exc:
            self._log.debug("idempotency auth rejected", err=repr(exc))
            return False
        scope.setdefault("state", {})["auth"] = identity
        scope[AUTH_DONE_KEY] = True
        return True

    async def _degrade(self, scope: Scope, receive: Receive, observed: ResponseTap) -> None:
        if self._cfg.fail_closed:
            await write_error(
                observed.send,
                unavailable("IDEMPOTENCY_UNAVAILABLE", "idempotency store is unavailable"),
            )
            return
        await self._app(scope, receive, observed.send)

    async def _in_progress(self, observed: ResponseTap, skey: str, fp: str) -> None:
        try:
            record = await self._load(skey)
        except StoreUnavailableError:
            record = None
        if record is not None:
            await self._replay(observed.send, record, fp)
            return
        self._count("in_progress")
        await write_error(
            observed.send,
            error(
                "IDEMPOTENCY_IN_PROGRESS",
                HTTPStatus.CONFLICT,
                f"a request with this {self._cfg.header} is still being processed",
            ),
        )

    async def _run(
        self,
        scope: Scope,
        receive: Receive,
        observed: ResponseTap,
        skey: str,
        token: str,
        fp: str,
    ) -> None:
        capture = _Capture(observed.send, self._cfg.max_body_bytes)
        try:
            await self._app(scope, receive, capture.send)
        except BaseException:
            # Исключение обработчика снимает резерв: иначе ретраи получают 409 до конца reserve_ttl.
            await self._release(skey, token)
            raise

        if observed.status >= HTTPStatus.INTERNAL_SERVER_ERROR or capture.overflow:
            await self._release(skey, token)
            self._count("skipped")
            return

        raw = json.dumps(
            {
                "fp": fp,
                "status": observed.status,
                "header": _replayable(observed.headers),
                "body": base64.b64encode(bytes(capture.buf)).decode(),
            }
        ).encode()
        try:
            await _guarded(self._store.set(skey, raw, self._cfg.ttl), self._cfg.store_timeout)
        except Exception as exc:
            self._log.warn("idempotency store failed", op="set", err=repr(exc))
            await self._release(skey, token)
            self._count("error")
            return
        self._count("stored")

    async def _replay(self, send: Send, record: dict[str, Any], fp: str) -> None:
        if record.get("fp") != fp:
            self._count("conflict")
            await write_error(
                send,
                error(
                    "IDEMPOTENCY_KEY_REUSED",
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    f"{self._cfg.header} was already used with a different request",
                ),
            )
            return
        body = base64.b64decode(str(record.get("body", "")))
        headers = [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, values in dict(record.get("header", {})).items()
            for value in values
        ]
        headers.append((HEADER_IDEMPOTENT_REPLAYED.lower().encode(), b"true"))
        # Длина известна: без неё ASGI-сервер уводил бы replay в chunked, хотя оригинал
        # уходил клиенту с Content-Length.
        headers.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": _status(record), "headers": headers})
        await send({"type": "http.response.body", "body": body})
        self._count("replayed")

    async def _load(self, skey: str) -> dict[str, Any] | None:
        try:
            raw = await _guarded(self._store.get(skey), self._cfg.store_timeout)
        except Exception as exc:
            self._log.warn("idempotency store failed", op="get", err=repr(exc))
            raise StoreUnavailableError("httpkit: idempotency store get failed") from exc
        if raw is None:
            return None
        try:
            decoded = json.loads(raw)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None

    async def _reserve(self, skey: str) -> str | None:
        try:
            token, ok = await _guarded(
                self._store.reserve(skey, self._cfg.reserve_ttl), self._cfg.store_timeout
            )
        except Exception as exc:
            self._log.warn("idempotency store failed", op="reserve", err=repr(exc))
            raise StoreUnavailableError("httpkit: idempotency store reserve failed") from exc
        return token if ok else None

    async def _release(self, skey: str, token: str) -> None:
        try:
            await _guarded(self._store.release(skey, token), self._cfg.store_timeout)
        except Exception as exc:
            self._log.warn("idempotency store failed", op="release", err=repr(exc))

    def _count(self, result: str) -> None:
        self._instr.idempotency.add(1, {"result": result})


def _status(record: dict[str, Any]) -> int:
    raw = record.get("status", 200)
    return int(raw) if isinstance(raw, int) else 200


def _query(scope: Scope) -> str:
    raw = scope.get("query_string", b"")
    return bytes(raw).decode("latin-1") if raw else ""


def _replayable(headers: list[tuple[bytes, bytes]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, value in headers:
        key = bytes(name).decode("latin-1").lower()
        if key in REPLAY_HEADERS:
            out.setdefault(key, []).append(bytes(value).decode("latin-1"))
    return out


async def _buffer_body(receive: Receive, limit: int) -> tuple[bytes, bool, list[Message]]:
    """Читает тело до `limit`; переполнение отдаёт запрос дальше без дедупликации."""
    chunks: list[bytes] = []
    consumed: list[Message] = []
    total = 0
    while True:
        message = await receive()
        consumed.append(message)
        if message["type"] != "http.request":
            break
        body = message.get("body", b"") or b""
        total += len(body)
        if total > limit:
            return b"", True, consumed
        chunks.append(body)
        if not message.get("more_body", False):
            break
    return b"".join(chunks), False, consumed


def _replay_receive(consumed: list[Message], receive: Receive) -> Receive:
    queue = list(consumed)

    async def wrapped() -> Message:
        if queue:
            return queue.pop(0)
        return await receive()

    return wrapped
