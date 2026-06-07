import pytest

from pylibinfra.storagekit import URLBuilder


@pytest.mark.parametrize(
    ("base", "bucket", "key", "want"),
    [
        ("https://cdn.example.com", "media", "abc.jpg", "https://cdn.example.com/media/abc.jpg"),
        ("https://cdn.example.com/", "media", "abc.jpg", "https://cdn.example.com/media/abc.jpg"),
        ("https://cdn.example.com///", "m", "k.pdf", "https://cdn.example.com/m/k.pdf"),
    ],
)
def test_build(base: str, bucket: str, key: str, want: str) -> None:
    assert URLBuilder(base, bucket).build(key) == want
