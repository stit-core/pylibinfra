import asyncio

from pylibinfra._internal import requestid
from pylibinfra.idkit import is_v7


def test_bind_unbind_roundtrip() -> None:
    assert requestid.current() == ""
    token = requestid.bind("abc")
    assert requestid.current() == "abc"
    requestid.unbind(token)
    assert requestid.current() == ""


async def test_isolated_per_task() -> None:
    async def child() -> str:
        return requestid.current()

    token = requestid.bind("parent")
    try:
        assert await asyncio.create_task(child()) == "parent"
    finally:
        requestid.unbind(token)


def test_valid() -> None:
    assert requestid.valid("req-1")
    assert not requestid.valid("")
    assert not requestid.valid("has space")
    assert not requestid.valid("tab\there")
    assert not requestid.valid("x" * (requestid.MAX_LEN + 1))
    assert requestid.valid("x" * requestid.MAX_LEN)


def test_new_is_v7() -> None:
    assert is_v7(requestid.new())
