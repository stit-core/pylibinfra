class URLBuilder:
    """Строит публичный стабильный URL `<base>/<bucket>/<key>`."""

    def __init__(self, base_url: str, bucket: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._bucket = bucket

    def build(self, key: str) -> str:
        return f"{self._base_url}/{self._bucket}/{key}"
