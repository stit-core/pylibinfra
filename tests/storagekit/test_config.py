from typing import Any

import pytest
from pydantic import ValidationError

from pylibinfra.storagekit import (
    BUCKET_LOOKUP_AUTO,
    CREDENTIALS_CHAIN,
    CREDENTIALS_STATIC,
    DEFAULT_PART_SIZE,
    DEFAULT_REGION,
    URL_STYLE_PATH,
    URL_STYLE_VIRTUAL,
    Config,
    ConfigError,
    StorageError,
    new_config,
)


def valid(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "endpoint": "s3.example.com:9000",
        "access_key": "key",
        "secret_key": "secret",
        "credentials": CREDENTIALS_STATIC,
        "bucket": "media",
        "bucket_lookup": BUCKET_LOOKUP_AUTO,
        "part_size": DEFAULT_PART_SIZE,
        "max_retries": 3,
        "probe_timeout": 5.0,
        "url_style": URL_STYLE_PATH,
        "public_base_url": "https://cdn.example.com",
    }
    values.update(overrides)
    return values


ACCEPTED: dict[str, dict[str, Any]] = {
    "host:port": {},
    "host": {"endpoint": "s3.example.com"},
    "https url": {"endpoint": "https://s3.example.com"},
    "http url": {"endpoint": "http://localhost:9000"},
    "trailing slash": {"endpoint": "https://s3.example.com/"},
    "ipv6 host": {"endpoint": "[::1]:9000"},
    "anonymous creds": {"access_key": "", "secret_key": ""},
    "chain creds": {"credentials": CREDENTIALS_CHAIN, "access_key": "", "secret_key": ""},
    "empty public base url": {"public_base_url": ""},
    "virtual url style": {"url_style": URL_STYLE_VIRTUAL},
    "zero probe timeout": {"probe_timeout": 0.0},
    "no retries": {"max_retries": 0},
}

REJECTED: dict[str, dict[str, Any]] = {
    "empty endpoint": {"endpoint": ""},
    "endpoint with path": {"endpoint": "https://s3.example.com/bucket"},
    "endpoint with query": {"endpoint": "https://s3.example.com?a=b"},
    "endpoint with creds": {"endpoint": "https://user:pass@s3.example.com"},
    "endpoint with unknown scheme": {"endpoint": "s3://s3.example.com"},
    "endpoint slash without scheme": {"endpoint": "s3.example.com/bucket"},
    "endpoint empty port": {"endpoint": "s3.example.com:"},
    "endpoint with two ports": {"endpoint": "s3.example.com:90:00"},
    "endpoint host is only a port": {"endpoint": "http://:9000"},
    "unparsable endpoint": {"endpoint": "https://[::1"},
    "http endpoint with use_ssl": {"endpoint": "http://s3.example.com", "use_ssl": True},
    "empty bucket": {"bucket": ""},
    "bucket with path": {"bucket": "media/uploads"},
    "half creds": {"secret_key": ""},
    "unknown creds source": {"credentials": "iam"},
    "unknown bucket lookup": {"bucket_lookup": "virtual"},
    "unknown url style": {"url_style": "cdn"},
    "part size below minimum": {"part_size": 1 << 20},
    "part size above maximum": {"part_size": 6 << 30},
    "negative retries": {"max_retries": -1},
    "negative probe timeout": {"probe_timeout": -1.0},
    "relative public base url": {"public_base_url": "cdn.example.com"},
    "unparsable public base url": {"public_base_url": "https://[::1"},
}


@pytest.mark.parametrize("overrides", ACCEPTED.values(), ids=list(ACCEPTED))
def test_accepted(overrides: dict[str, Any]) -> None:
    assert Config(**valid(**overrides))


@pytest.mark.parametrize("overrides", REJECTED.values(), ids=list(REJECTED))
def test_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Config(**valid(**overrides))
    with pytest.raises(ConfigError):
        new_config(**valid(**overrides))


def test_zero_value_config_is_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        new_config()
    assert "storagekit: invalid config" in str(excinfo.value)
    assert isinstance(excinfo.value, StorageError)
    assert isinstance(excinfo.value, ValueError)


def test_config_error_does_not_leak_secrets() -> None:
    with pytest.raises(ConfigError) as excinfo:
        new_config(**valid(secret_key=""))
    assert "key" not in str(excinfo.value).replace("STORAGE_S3_ACCESS_KEY", "")


def test_credentials_stay_out_of_repr() -> None:
    cfg = new_config(**valid(access_key="ak-sup3rsecret", secret_key="sk-sup3rsecret"))

    assert "sup3rsecret" not in repr(cfg)
    assert "sup3rsecret" not in str(cfg)
    assert cfg.secret_key.get_secret_value() == "sk-sup3rsecret"


def test_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "media")

    cfg = new_config()

    assert cfg.credentials == CREDENTIALS_STATIC
    assert cfg.bucket_lookup == BUCKET_LOOKUP_AUTO
    assert cfg.part_size == DEFAULT_PART_SIZE == 16 << 20
    assert cfg.max_retries == 3
    assert cfg.probe_timeout == 5.0
    assert cfg.probe_on_start is True
    assert cfg.create_bucket is False
    assert cfg.url_style == URL_STYLE_PATH
    assert cfg.use_ssl is False
    assert cfg.region == ""
    assert cfg.public_base_url == ""
    assert cfg.access_key.get_secret_value() == ""


def test_env_values_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "media")
    monkeypatch.setenv("STORAGE_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("STORAGE_S3_SECRET_KEY", "sk")
    monkeypatch.setenv("STORAGE_S3_PART_SIZE", str(5 << 20))
    monkeypatch.setenv("STORAGE_S3_PROBE_ON_START", "false")
    monkeypatch.setenv("STORAGE_S3_URL_STYLE", "virtual")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example.com")

    cfg = new_config()

    assert cfg.secret_key.get_secret_value() == "sk"
    assert cfg.part_size == 5 << 20
    assert cfg.probe_on_start is False
    assert cfg.url_style == URL_STYLE_VIRTUAL
    assert cfg.public_base_url == "https://cdn.example.com"


def test_invalid_env_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "media")
    monkeypatch.setenv("STORAGE_S3_MAX_RETRIES", "not-a-number")

    with pytest.raises(ConfigError):
        new_config()


ENDPOINTS: dict[str, tuple[dict[str, Any], str]] = {
    "host:port keeps use_ssl": ({"endpoint": "s3.example.com:9000"}, "http://s3.example.com:9000"),
    "host:port with use_ssl": (
        {"endpoint": "s3.example.com:9000", "use_ssl": True},
        "https://s3.example.com:9000",
    ),
    "https scheme wins": ({"endpoint": "https://s3.example.com"}, "https://s3.example.com"),
    "http scheme wins": ({"endpoint": "http://localhost:9000"}, "http://localhost:9000"),
    "trailing slash allowed": ({"endpoint": "https://s3.example.com/"}, "https://s3.example.com"),
}


@pytest.mark.parametrize(("overrides", "want"), ENDPOINTS.values(), ids=list(ENDPOINTS))
def test_endpoint_url(overrides: dict[str, Any], want: str) -> None:
    assert Config(**valid(**overrides)).endpoint_url() == want


def test_signing_region() -> None:
    assert Config(**valid()).signing_region() == DEFAULT_REGION
    assert Config(**valid(region="eu-central-1")).signing_region() == "eu-central-1"


def test_config_is_frozen() -> None:
    cfg = Config(**valid())
    with pytest.raises(ValidationError):
        cfg.bucket = "other"  # type: ignore[misc]


def test_new_config_values_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_BUCKET", "from-env")
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "localhost:9000")

    assert new_config(bucket="explicit").bucket == "explicit"


def test_config_ignores_foreign_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "media")
    monkeypatch.setenv("SOME_OTHER_VAR", "x")

    assert new_config().bucket == "media"


def test_field_names_are_public_api() -> None:
    assert set(Config.model_fields) == {
        "endpoint",
        "access_key",
        "secret_key",
        "credentials",
        "bucket",
        "region",
        "use_ssl",
        "bucket_lookup",
        "create_bucket",
        "part_size",
        "max_retries",
        "probe_timeout",
        "probe_on_start",
        "url_style",
        "public_base_url",
    }
