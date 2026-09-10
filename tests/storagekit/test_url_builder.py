import pytest

from pylibinfra.storagekit import URL_STYLE_PATH, URL_STYLE_VIRTUAL, URLBuilder
from pylibinfra.storagekit.url_builder import escape_key

CASES: dict[str, tuple[str, str, str, str, str]] = {
    "path style": (
        "https://cdn.example.com",
        "media",
        URL_STYLE_PATH,
        "abc.jpg",
        "https://cdn.example.com/media/abc.jpg",
    ),
    "trailing slashes collapse": (
        "https://cdn.example.com///",
        "m",
        URL_STYLE_PATH,
        "k.pdf",
        "https://cdn.example.com/m/k.pdf",
    ),
    "empty style is path": (
        "https://cdn.example.com/",
        "media",
        "",
        "abc.jpg",
        "https://cdn.example.com/media/abc.jpg",
    ),
    "virtual style omits bucket": (
        "https://media.cdn.example.com",
        "media",
        URL_STYLE_VIRTUAL,
        "abc.jpg",
        "https://media.cdn.example.com/abc.jpg",
    ),
    "nested key keeps separators": (
        "https://cdn.example.com",
        "media",
        URL_STYLE_PATH,
        "2026/07/abc.jpg",
        "https://cdn.example.com/media/2026/07/abc.jpg",
    ),
    "unsafe characters are escaped": (
        "https://cdn.example.com",
        "media",
        URL_STYLE_PATH,
        "a b#c?d.txt",
        "https://cdn.example.com/media/a%20b%23c%3Fd.txt",
    ),
    "empty bucket is skipped": (
        "https://cdn.example.com",
        "",
        URL_STYLE_PATH,
        "abc.jpg",
        "https://cdn.example.com/abc.jpg",
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_build(case: tuple[str, str, str, str, str]) -> None:
    base, bucket, style, key, want = case
    assert URLBuilder(base, bucket, style).build(key) == want


def test_default_style_is_path() -> None:
    assert URLBuilder("https://cdn.example.com", "media").build("k") == (
        "https://cdn.example.com/media/k"
    )


def test_escape_key_keeps_separators() -> None:
    assert escape_key("a b/c#d") == "a%20b/c%23d"
    assert escape_key("plain.jpg") == "plain.jpg"
