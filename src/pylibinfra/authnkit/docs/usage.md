# authnkit — рецепты

Точные сигнатуры публичного API и типовые сценарии.

## Сигнатуры

```python
# Hashing
class Hasher(Protocol):
    async def hash(self, plain: str) -> str: ...
    async def verify(self, plain: str, encoded: str) -> bool: ...
    def needs_rehash(self, encoded: str) -> bool: ...
    async def verify_dummy(self) -> None: ...


class Argon2idHasher:  # реализует Hasher
    def __init__(self, params: Argon2idParams) -> None: ...


# ConfigError, InvalidHashError, HashParamsError (наследник InvalidHashError)


# JWT
class JWTSigner(Protocol):
    def sign(self, claims: dict[str, Any], ttl: float) -> str: ...  # ttl — секунды
    def sign_access(self, claims: dict[str, Any]) -> str: ...  # ttl = JWTConfig.access_ttl
    def sign_refresh(self, claims: dict[str, Any]) -> str: ...  # ttl = JWTConfig.refresh_ttl


class JWTParser(Protocol):
    def parse(self, token: str) -> dict[str, Any]: ...  # тип токена не проверяется
    def parse_access(self, token: str) -> dict[str, Any]: ...  # typ == "access"
    def parse_refresh(self, token: str) -> dict[str, Any]: ...  # typ == "refresh"


class HS256JWT:  # реализует и JWTSigner, и JWTParser
    def __init__(
        self,
        cfg: JWTConfig,
        *,
        clock: Clock | None = None,  # default: clockkit.Real()
        issuer: str | None = None,
        audience: str | Sequence[str] | None = None,
    ) -> None: ...


# CLAIM_TOKEN_TYPE == "typ"; значения TOKEN_TYPE_ACCESS / TOKEN_TYPE_REFRESH
# TokenError → TokenExpiredError, TokenMalformedError, TokenSignatureError,
#              WrongTokenTypeError, TokenAudienceError


# TOTP
class TOTP:
    def __init__(
        self,
        cfg: TOTPConfig,
        *,
        clock: Clock | None = None,
        used_codes: UsedCodeStore | None = None,
    ) -> None: ...
    def generate(self, account: str) -> tuple[str, str]: ...  # (secret, otpauth_url)
    def verify(self, secret: str, code: str) -> bool: ...
    async def verify_once(self, secret: str, code: str) -> bool: ...  # требует used_codes
    @property
    def code_ttl(self) -> float: ...  # (2*skew + 1) * TOTP_PERIOD


class UsedCodeStore(Protocol):
    async def mark_used(self, key: str, ttl: float) -> bool: ...


class MemoryUsedCodeStore:
    def __init__(self, *, clock: Clock | None = None) -> None: ...


class CacheUsedCodeStore:
    def __init__(self, cache: UsedCodeCache) -> None: ...  # cachekit.Cache подходит структурно


# TOTPError → InvalidSecretError, UsedCodeStoreError

# RBAC
Permission = str


class Registry:
    def register(self, role: str, *perms: Permission) -> None: ...
    def has(self, role: str, perm: Permission) -> bool: ...


# ENV-фабрики конфигов
def new_argon2id_params() -> Argon2idParams: ...
def new_jwt_config() -> JWTConfig: ...
def new_totp_config() -> TOTPConfig: ...
```

Корутины — только там, где есть настоящая работа: Argon2id уходит в
`asyncio.to_thread`, `verify_once` ходит в `UsedCodeStore`. Остальное считает
HMAC на месте и остаётся обычными методами.

## Хэш пароля

```python
hasher = Argon2idHasher(params)  # ConfigError — параметры вне окна, падаем на старте

encoded = await hasher.hash(plain_password)  # храни encoded целиком, он самоописывающий

try:
    ok = await hasher.verify(submitted_password, encoded)
except InvalidHashError:
    ...  # encoded битый/чужого формата/вне окна — не «пароль неверен»

if ok and hasher.needs_rehash(encoded):
    encoded = await hasher.hash(submitted_password)  # параметры подняли — обновить запись
```

`encoded` хранится в БД как есть — соль и параметры внутри строки. Соль на
каждый `hash` новая, поэтому одинаковые пароли дают разные строки.

## Логин без timing-оракула

Путь «пользователя нет» отвечает за микросекунды, путь «пароль неверен» — за
десятки-сотни миллисекунд: разница видна снаружи и перечисляет учётные записи.
`verify_dummy` выравнивает время.

```python
user = await repo.by_email(email)
if user is None:
    await hasher.verify_dummy()  # столько же CPU, сколько verify
    raise InvalidCredentialsError

try:
    ok = await hasher.verify(submitted_password, user.password_hash)
except InvalidHashError:
    ok = False  # битый хэш в базе логируем, наружу — тот же ответ
if not ok:
    raise InvalidCredentialsError  # одинаковый ответ на оба случая
```

## Sign / verify JWT

```python
jwt = HS256JWT(cfg)  # cfg.secret ≥ 32 байт, иначе ConfigError

access = jwt.sign_access({"sub": user_id, "role": "admin"})
refresh = jwt.sign_refresh({"sub": user_id})
invite = jwt.sign({"sub": user_id, "typ": "invite"}, 24 * 3600)  # нестандартный TTL

try:
    claims = jwt.parse_access(access)
    sub = claims["sub"]
except WrongTokenTypeError:
    ...  # предъявили refresh (или чужой) токен там, где нужен access
except TokenExpiredError:
    ...  # отправить на refresh
except TokenSignatureError:
    ...  # чужой/подделанный токен
except TokenAudienceError:
    ...  # токен другого сервиса на общем секрете
except TokenMalformedError:
    ...  # мусор на входе, не-HS256 alg, нет exp
```

`exp`, `iat` и (для `sign_access`/`sign_refresh`) `typ` **перезаписываются**
китом — значения из `claims` для этих ключей не сохраняются. `claims` должны
быть JSON-сериализуемы: иначе `sign` поднимает `TypeError` от json.

Токены со своей схемой (приглашения, одноразовые ссылки) выпускай через `sign`
со своим `typ` и разбирай через `parse`, проверяя `typ` в коде flow'а:
`parse_access`/`parse_refresh` пропускают только собственные типы кита.

### Изоляция по iss/aud

Общий `JWT_SECRET` на несколько сервисов означает, что подпись сходится везде.
`issuer=`/`audience=` привязывают токен к выпускающему и принимающему:

```python
issuer = HS256JWT(cfg, issuer="auth", audience="billing")
# billing принимает только токены с aud=billing
billing = HS256JWT(cfg, audience="billing")
```

Аргументы симметричны: заданный `iss`/`aud` и проставляется при `sign*`, и
требуется при `parse*` (иначе `TokenAudienceError`). Не заданы — не проверяются.
`audience` списком проставляет `aud` массивом, а при разборе достаточно
пересечения.

## TOTP (2FA)

```python
# ConfigError: пустой issuer, ':' или '/' в нём, skew > 2
totp = TOTP(TOTPConfig(issuer="MyService", skew=1))

# enrollment: показать пользователю QR из otpauth_url, secret сохранить у себя
secret, otpauth_url = totp.generate(user.email)

# логин: проверить введённый код
try:
    ok = totp.verify(secret, submitted_code)
except InvalidSecretError:
    ...  # секрет в хранилище нечитаем — поломка данных, не «неверный код»
```

`verify` принимает код для текущего и `±skew` соседних 30-секундных окон
(толеранс к рассинхрону часов).

### Однократность кода (replay)

`verify_once` отвергает код, уже принятый в пределах его окна:

```python
totp = TOTP(cfg, used_codes=MemoryUsedCodeStore())  # одна реплика
ok = await totp.verify_once(secret, submitted_code)
```

Нескольким репликам нужен общий store — `CacheUsedCodeStore` поверх
`cachekit.Cache` (подходит структурно, импорт cachekit ките не нужен):

```python
totp = TOTP(cfg, used_codes=CacheUsedCodeStore(cache))
```

Ключ кит считает сам (SHA-256 от секрета и кода — сам секрет в общий Redis не
уезжает), TTL — длина окна с учётом `skew` (`TOTP.code_ttl`). Ошибка store →
`UsedCodeStoreError`: пускать ли пользователя при недоступном хранилище, решает
потребитель. Без `used_codes` `verify_once` поднимает `ConfigError`.

## RBAC

```python
# На старте приложения — наполнить реестр ролей (из контейнера: Registry).
roles = Registry()
roles.register("admin", "users:read", "users:write")
roles.register("viewer", "users:read")

# В обработчике — проверить право роли из токена.
if not roles.has(role, "users:write"):
    raise Forbidden  # fail-closed: неизвестная роль/право → False
```

Реестр потокобезопасен. Повторный `register` для той же роли полностью
перезаписывает её набор прав.

## Подключение через dishka

```python
container = make_async_container(
    # provides: Hasher, Argon2idHasher, HS256JWT, JWTSigner, JWTParser, Registry
    AuthnProvider(),
    # provides: TOTP (+ дефолт OptionalUsedCodeStore = None) — только если нужен 2FA
    TOTPProvider(),
    # Clock: оба провайдера берут часы из контейнера
    ClockProvider(),
    # Провайдеры НЕ отдают Argon2idParams / JWTConfig / TOTPConfig — регистрируем сами
    AppProvider(),
    # Свой UsedCodeStore — провайдером ПОСЛЕ TOTPProvider (override дефолта)
    CacheProvider(),
)
```

Невалидный конфиг валит первый `container.get` (`ConfigError`), а не первый
`hash`/`sign`. `iss`/`aud` провайдер не проставляет: сервису, которому нужна
изоляция по aud, удобнее собрать `HS256JWT` своим провайдером с
`issuer=`/`audience=`.

## Инварианты и подводные камни

- **Ошибка `verify` ≠ «пароль неверен».** `Hasher.verify` поднимает
  `InvalidHashError` при битой/чужой кодировке и возвращает `False` при
  несовпадении пароля. Логируй ошибку, но пользователю всегда отдавай
  одинаковый «неверные учётные данные».
- **Окно параметров Argon2id.** PHC-строка вне окна (`t` вне `1..64`, `p` вне
  `1..16`, `m` вне `8 МиБ..1 ГиБ`, произведение `t*m` больше
  `MAX_COST_KIB_PASSES` = 786432 КиБ·прохода, соль/хэш вне `8..64`/`16..128`
  байт) → `HashParamsError` (наследник `InvalidHashError`) **до** вычисления.
  Потолок стоимости ограничивает и CPU, и RAM одной проверки. Строка должна
  быть канонической — пробелы, переводы строк, ведущие нули в числах и
  неканоничный хвост base64 отвергаются.
- **Неизвестный пользователь — `verify_dummy`.** Иначе время ответа перечисляет
  учётные записи.
- **Хэширование занимает поток.** Каждый `hash`/`verify`/`verify_dummy` держит
  поток из пула `asyncio.to_thread` на всё время вычисления (дефолт —
  `min(32, cpu_count + 4)` потоков). Всплеск логинов упирается в этот пул, а не
  в event-loop; калибруй параметры под целевое время, см. configuration.md.
- **Стойкость Argon2id растёт со временем, безопасно для старых хэшей.**
  Параметры зашиты в PHC-строку, поэтому поднятие `Argon2idParams` не ломает
  проверку ранее выданных хэшей. Перевыпуск — `needs_rehash` + `hash` на
  успешном логине, на стороне потребителя.
- **Тип токена — часть контракта.** `sign_access`/`sign_refresh` пишут
  `typ=access|refresh`, `parse_access`/`parse_refresh` требуют совпадения:
  refresh-токен в access-контуре → `WrongTokenTypeError`. `parse` тип не
  проверяет — используй его только для собственных схем токенов.
- **JWT: TTL из конфига или явно.** `sign(claims, ttl)` с `ttl <= 0` →
  `ConfigError` (а не токен, живущий один leeway).
- **JWT clock skew.** `parse*` принимает токен ещё `leeway` (default 60 с) после
  `exp` и допускает `nbf`/`iat` на `leeway` в будущем. Часы между сервисами всё
  равно синхронизируй (NTP).
- **alg-confusion закрыт.** `parse*` принимает только `HS256`; токен с
  `alg: none`, асимметричным или другим HMAC-алгоритмом → `TokenMalformedError`.
  `exp` обязателен. Секрет HS256 общий для подписи и проверки — храни его как
  секрет, не короче 32 байт.
- **Числа в claims — `int`.** `exp`/`iat` кит проставляет целыми секундами;
  чужие числовые claims приходят такими, какими их положил подписант.
- **TOTP-окно ±skew шагов.** С `skew=1` код «живёт» ~90 с; потолок — 2
  (`MAX_TOTP_SKEW`), выше окно brute-force растёт линейно. Однократность кода
  даёт `verify_once` с `UsedCodeStore`; голый `verify` от replay не защищает.
- **`MemoryUsedCodeStore` — на одну реплику.** Код, принятый на одной реплике,
  на соседней пройдёт снова; нескольким репликам нужен `CacheUsedCodeStore`.
- **RBAC fail-closed и экземплярный.** Неизвестная роль/право → `False`. Реестр
  — экземпляр из контейнера; process-global состояния нет.
