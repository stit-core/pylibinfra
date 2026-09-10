# authnkit — устройство

`authnkit` — набор не связанных между собой примитивов аутентификации. Кит
сознательно не строит flow: нет сессий, refresh-ротации, account lockout,
enrollment'а MFA, хранилища секретов. Это «кирпичики», из которых потребитель
собирает свой auth-домен, и каждый из них криптографически корректен по
умолчанию.

```
authnkit/
├── __init__.py       package-doc и публичный экспорт
├── errors.py         ConfigError / InvalidHashError / TokenError / TOTPError и потомки
├── hasher.py         Hasher: Argon2idHasher (хэш пароля, needs_rehash, verify_dummy, разбор PHC)
├── jwt.py            JWTSigner/JWTParser: HS256JWT (подпись и разбор токена, pyjwt)
├── totp.py           TOTP (RFC 6238, обёртка pyotp)
├── usedcode.py       UsedCodeStore + MemoryUsedCodeStore / CacheUsedCodeStore
├── permissions.py    RBAC: Permission / Registry (экземплярный реестр)
├── metrics.py        OTel-инструменты (создаются в конструкторах через _internal.otelkit)
└── module.py         AuthnProvider, TOTPProvider, ENV-фабрики конфигов
```

## Четыре примитива

### Hashing — Argon2id

`Hasher` — Protocol из четырёх методов (`hash`, `verify`, `needs_rehash`,
`verify_dummy`). Единственная реализация — `Argon2idHasher` поверх
`argon2.low_level.hash_secret_raw` (Argon2**id**-вариант, версия 19).

- `hash` генерирует случайную соль (`secrets.token_bytes`, длина `salt_len`) и
  кодирует результат в самоописывающую PHC-строку:
  `$argon2id$v=19$m=<memory>,t=<time>,p=<threads>$<salt-b64>$<hash-b64>`.
  Base64 — стандартный алфавит без padding, декодируется строго.
- `verify` парсит эту строку, **извлекает параметры из самого хэша** (memory,
  time, threads, длину ключа), пересчитывает Argon2id и сравнивает в постоянном
  времени (`hmac.compare_digest`).
- `needs_rehash(encoded)` — параметры строки отличаются от параметров хэшера
  (или строка нечитаема): сигнал перехэшировать пароль после успешного логина.
- `verify_dummy()` — тот же объём вычислений без сравнения: путь «пользователь
  не найден» отвечает столько же, сколько «пароль неверен», иначе время ответа
  перечисляет учётные записи.

`hash`, `verify` и `verify_dummy` — корутины: сам Argon2id считается в
`asyncio.to_thread`, потому что одно вычисление занимает десятки миллисекунд
CPU и в event-loop'е останавливало бы обслуживание всех соединений процесса.
`needs_rehash` только разбирает строку и остаётся обычным методом.

WHY параметры зашиты в хэш: старые хэши проверяются корректно даже после того,
как `Argon2idParams` подняли (стойкость растёт со временем). Соль уникальна на
каждый `hash` — два вызова с одним паролем дают разные строки.

**Окно параметров.** Параметры PHC-строки приходят из хранилища, а значит
задавали бы CPU и RAM одной проверки. И `hash`, и `verify` принимают только
`t` в `1..64`, `p` в `1..16`, `m` в `8 МиБ..1 ГиБ`, соль `8..64` байт, хэш
`16..128` байт (константы `MIN_*`/`MAX_*`) — и сверх того ограничивают
**стоимость**: `t * m` не больше `MAX_COST_KIB_PASSES` (786432 КиБ·прохода —
четыре дефолтных хэша). Каждая граница по отдельности пропускает и `t=64`, и
`m=1 ГиБ`, а вместе они стоят ~10 с и гигабайт на один `verify`. Проверка окна
идёт **до** вычисления, поэтому строка с `t=2^31` отвергается мгновенно.
Строка вне окна, чужой `v=`, битый или неканоничный base64 (мусор в хвостовых
битах — это другое представление того же хэша), неканоническое число
(`m=0065536`), пробел или перевод строки, лишние или недостающие поля, иной
порядок `m,t,p` → `InvalidHashError`. Параметры самого хэшера вне окна →
`Argon2idHasher` поднимает `ConfigError` на старте приложения.

### JWT — HS256

`HS256JWT` реализует `JWTSigner` (`sign`) и `JWTParser` (`parse`) поверх
`pyjwt`. Алгоритм — симметричный HMAC-SHA256; секрет общий для подписи и
проверки (`JWTConfig.secret`, не короче 32 байт — иначе `ConfigError`).

- `sign(claims, ttl)` копирует переданные claims, проставляет `exp = now+ttl` и
  `iat = now`; `ttl <= 0` → `ConfigError`. `sign_access` / `sign_refresh` — то же
  с `JWTConfig.access_ttl` / `refresh_ttl` плюс claim `typ`.
- `parse(token)` верифицирует подпись и сроки. Алгоритм закреплён: принимается
  только `alg: HS256` (`none`, RS*, HS384/512 → `TokenMalformedError`). `exp`
  обязателен — токен без него `TokenMalformedError`. `exp`/`nbf`/`iat` сверяются
  с leeway (`JWTConfig.leeway`, default 60 с).
- `parse_access` / `parse_refresh` дополнительно требуют `typ=access|refresh`
  (`WrongTokenTypeError`).
- `issuer=` / `audience=` включают симметричную проверку `iss`/`aud`: что
  проставлено при подписи, то требуется при разборе (`TokenAudienceError`).
- Часы — `clockkit.Clock` (аргумент `clock=`); по умолчанию `clockkit.Real`.

WHY сроки считает кит, а не pyjwt: `jwt.decode` берёт «сейчас» из системных
часов, и подменить их в тесте нечем. Кит отключает проверки сроков pyjwt
(`verify_exp`/`verify_nbf`/`verify_iat`), оставляет ей подпись, pin алгоритма и
обязательность `exp`, а `exp`/`nbf`/`iat` сверяет с `Clock.now()` сам.

WHY тип токена внутри кита: `sign_access`/`sign_refresh` различались бы только
сроком жизни, и refresh-токен проходил бы проверку access-контура. Проверку
типа нельзя оставлять потребителю — кит, который выпускает два вида токенов,
обязан их и различать.

### TOTP — RFC 6238

`TOTP` — тонкая обёртка над `pyotp`. Конфиг — `TOTPConfig(issuer, skew)`.

- `generate(account)` создаёт новый секрет и возвращает его base32-форму +
  `otpauth://`-URL (его можно отдать как QR в authenticator-приложение).
  Параметры заданы явно (период 30 с, 6 цифр, SHA1) — теми же, по которым
  считает `verify`; `:` и `/` в issuer и account отвергаются, иначе label URL
  разбирается приложениями неоднозначно.
- `verify(secret, code)` проверяет код в окне `±skew` шагов (`TOTP_SKEW`,
  default 1, потолок 2 — толеранс к рассинхрону часов и задержке ввода).
  Нечитаемый секрет из хранилища — `InvalidSecretError`, а не «код неверен».

**Replay.** Внутри окна один и тот же код валиден несколько раз. `verify_once`
закрывает это через `UsedCodeStore` (ключ — SHA-256 от секрета и кода, TTL —
длина окна с учётом `skew`): `MemoryUsedCodeStore` для одной реплики,
`CacheUsedCodeStore` поверх `cachekit.Cache.set_nx` — для нескольких. Голый
`verify` состояния не хранит и от replay не защищает.

`verify_once` — единственная корутина примитива: она ходит в хранилище, которое
может быть общим (Redis). `generate` и `verify` считают HMAC на месте и
остаются обычными методами.

### RBAC — экземплярный реестр прав

`Registry` — простейший статический RBAC: соответствие «роль → набор прав» под
`threading.RLock`. Провайдится `AuthnProvider` (один на приложение), в тестах
создаётся `Registry()` на тест — общего process-global состояния нет.

- `register(role, *perms)` записывает набор прав роли (полностью перезаписывает
  предыдущий для этой роли).
- `has(role, perm)` — потокобезопасная проверка. Неизвестная роль или право →
  `False` (fail-closed).

WHY статично: это базовый guard для ролей, известных на старте приложения.
Динамические права на пользователя, иерархии ролей, скоупы из БД — это уже
доменная логика потребителя, кит её не навязывает.

## Observability

Инструменты создаются в конструкторах (`Argon2idHasher`, `HS256JWT`, `TOTP`)
через `_internal.otelkit` и глобальный провайдер OTel. Глобальный провайдер —
делегирующий: инструменты, созданные до `set_meter_provider`, переезжают на
реальный провайдер при его регистрации, поэтому порядок «конструктор →
провайдер» не критичен. Полный справочник сигналов — [observability.md](observability.md).

## dishka и конфигурация

`AuthnProvider` провайдит `Argon2idHasher` и `Hasher`, `HS256JWT` и его порты
(`JWTSigner`, `JWTParser`), `Registry`. Второй фактор живёт в отдельном
`TOTPProvider` (`TOTP` + дефолт `OptionalUsedCodeStore = None`).

WHY два провайдера: dishka проверяет граф целиком на сборке контейнера — не
лениво, как fx. В одном провайдере `TOTPConfig` (а значит и обязательный
`TOTP_ISSUER`) требовался бы и от приложений без 2FA.

Сами конфиги провайдеры **не** отдают — их регистрирует приложение (типично
через App-Provider), однородно с `httpkit`/`pgkit`. Для приложений без
App-pattern есть ENV-фабрики `new_argon2id_params` / `new_jwt_config` /
`new_totp_config`. Часы оба провайдера берут из контейнера — консьюмер
регистрирует `clockkit.ClockProvider`.

Детали ENV — [configuration.md](configuration.md); рецепты подключения —
[usage.md](usage.md).
