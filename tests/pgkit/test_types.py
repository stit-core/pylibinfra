from uuid import UUID

import pytest

from pylibinfra.pgkit import InvalidUUIDError, parse_uuid, unmarshal_jsonb


def test_parse_uuid_roundtrip() -> None:
    raw = "0195a3f0-0000-7000-8000-000000000000"
    assert parse_uuid(raw) == UUID(raw)


def test_parse_uuid_rejects_empty() -> None:
    with pytest.raises(InvalidUUIDError, match="empty uuid"):
        parse_uuid("")


def test_parse_uuid_rejects_garbage() -> None:
    with pytest.raises(InvalidUUIDError, match="invalid uuid"):
        parse_uuid("not-a-uuid")


@pytest.mark.parametrize("empty", [None, "", b""])
def test_unmarshal_jsonb_empty_is_none(empty) -> None:
    assert unmarshal_jsonb(empty) is None


def test_unmarshal_jsonb_decodes() -> None:
    assert unmarshal_jsonb('{"a": 1}') == {"a": 1}
    assert unmarshal_jsonb(b"[1, 2]") == [1, 2]


def test_unmarshal_jsonb_reports_bad_payload() -> None:
    with pytest.raises(ValueError, match="pgkit: unmarshal jsonb"):
        unmarshal_jsonb("{oops")
