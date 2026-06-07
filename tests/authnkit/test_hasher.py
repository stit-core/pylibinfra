from pylibinfra.authnkit import Argon2idHasher, Argon2idParams


def _hasher() -> Argon2idHasher:
    return Argon2idHasher(
        Argon2idParams(
            ARGON2_TIME=1,
            ARGON2_MEMORY_KIB=8,
            ARGON2_THREADS=1,
            ARGON2_KEYLEN=32,
            ARGON2_SALTLEN=16,
        )
    )


def test_hash_verify_roundtrip() -> None:
    h = _hasher()
    encoded = h.hash("abc")
    assert encoded.startswith("$argon2id$")
    assert h.verify("abc", encoded) is True


def test_verify_wrong_password() -> None:
    h = _hasher()
    encoded = h.hash("abc")
    assert h.verify("wrong", encoded) is False


def test_hash_salt_is_random() -> None:
    h = _hasher()
    assert h.hash("abc") != h.hash("abc")


def test_verify_garbage_string() -> None:
    h = _hasher()
    assert h.verify("abc", "notahash") is False
