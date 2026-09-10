# authnkit — тестирование

Тестовый хелпер живёт в сабпакете `testkit/authntest` (вне самого кита,
однородно с другими `testkit/*test` сабпакетами).

```python
from pylibinfra.testkit.authntest import HASH_PREFIX, DeterministicHasher, strip_hash_prefix
```

## DeterministicHasher

Реализация `authnkit.Hasher` без криптографии: подставляется в доменные тесты
вместо `Argon2idHasher`, чтобы они не платили десятки миллисекунд на каждый хэш
и были детерминированными.

```python
hasher = DeterministicHasher()
encoded = await hasher.hash("secret")  # "hash:secret"
assert await hasher.verify("secret", encoded)  # True
assert strip_hash_prefix(encoded) == "secret"
assert hasher.needs_rehash(encoded) is False  # параметров у фейка нет
await hasher.verify_dummy()  # no-op
```

Хэш предсказуем и не секретен — только для тестов. Предохранитель на случай
ошибки проводки dishka: вне pytest (нет `PYTEST_CURRENT_TEST` в окружении)
любой метод поднимает `RuntimeError`, а не кладёт пароли в базу открытым
текстом. Детали и подмена в контейнере —
[testkit/authntest/README.md](../../testkit/authntest/README.md).

## JWT и TOTP: управляемые часы

`HS256JWT`, `TOTP` и `MemoryUsedCodeStore` принимают `clock=`: с
`clockkit.Fixed` / `clockkit.Tickable` проверка `exp`, leeway, TOTP-окон и
истечения записей об использованных кодах становится детерминированной без
`asyncio.sleep`.

```python
clock = Tickable(datetime.fromtimestamp(1_700_000_000, UTC))
jwt = HS256JWT(cfg, clock=clock)
token = jwt.sign_access({"sub": "u"})

clock.advance(timedelta(minutes=30))
with pytest.raises(TokenExpiredError):
    jwt.parse_access(token)
```

Валидный TOTP-код на момент часов генерируй тем же алгоритмом, каким считает
кит:

```python
code = pyotp.TOTP(secret, digits=TOTP_DIGITS, digest=hashlib.sha1, interval=TOTP_PERIOD).at(
    clock.now()
)
assert totp.verify(secret, code) is True
```

Секрет в тестовых `JWTConfig` — не короче 32 байт, иначе конструктор поднимет
`ConfigError`; то же с `TOTPConfig.issuer` (непустой, без `:` и `/`) и
`Argon2idParams` (окно).

## Быстрые параметры Argon2id в тестах

Дефолтные `Argon2idParams` (64 МиБ, 3 прохода) стоят ~100 мс на хэш. Тесту, где
важна сама проверка, а не стойкость, хватает нижней границы окна:

```python
params = Argon2idParams(time=1, memory_kib=8192, threads=1, key_len=16, salt_len=8)
```

Ниже окно не пускает — это тот же барьер, что защищает `verify` от параметров
из хранилища.

## Метрики

Метрики кита снимаются общими фикстурами `tests/conftest.py` (`metric_reader`,
`read_metric`). Инструменты создаются в конструкторах примитивов, поэтому
`Argon2idHasher`/`HS256JWT`/`TOTP` в тесте создавай **после** того, как фикстура
поставила `MeterProvider`:

```python
async def test_metrics(read_metric):
    jwt = HS256JWT(cfg)  # инструменты берут провайдер фикстуры
    jwt.parse(jwt.sign_access({"sub": "u"}))
    assert read_metric("authn_jwt_verifications_total", {"result": "ok"}) == 1
```

## RBAC

`Registry` экземплярный — в каждом тесте свой `Registry()`, общего состояния
между тестами нет, параллелить безопасно.

## Фейковый UsedCodeStore

`UsedCodeStore` — Protocol из одного метода: store, который всегда падает
(проверка ветки `store_error`) или всегда отдаёт `False` (ветка `replay`),
пишется на месте, отдельного хелпера для него нет.

```python
class FailingStore:
    async def mark_used(self, key: str, ttl: float) -> bool:
        raise RuntimeError("store down")
```
