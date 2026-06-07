import time
from uuid import UUID

from pylibinfra.storagekit import new_key


def test_new_key_ext_by_content_type() -> None:
    cases = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "application/octet-stream": "",
        "": "",
    }
    for content_type, want_ext in cases.items():
        key = new_key(content_type)
        assert key.endswith(want_ext)
        ident = key.removesuffix(want_ext) if want_ext else key
        parsed = UUID(ident)
        assert parsed.version == 7


def test_new_key_unique() -> None:
    seen = {new_key("image/png") for _ in range(100)}
    assert len(seen) == 100


def test_new_key_k_sortable() -> None:
    # uuidv7 monotonic только между разными миллисекундами — внутри одной ms
    # порядок задают random-биты, поэтому разносим генерацию по времени.
    keys = []
    for _ in range(5):
        keys.append(new_key("image/png"))
        time.sleep(0.002)
    assert keys == sorted(keys)
