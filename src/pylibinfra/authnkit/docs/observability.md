# authnkit — observability

Единый источник правды по сигналам кита. Имена, типы и labels берутся из
`metrics.py` и точек записи в `jwt.py` / `hasher.py` / `totp.py`.

Meter: `pylibinfra.authnkit` (через `_internal.otelkit`). Спанов кит не эмитит,
логов не пишет: примитивы синхронны по отношению к вызывающему и не имеют
фонового цикла, о котором стоило бы рассказывать.

## Метрики

| Метрика | Тип | Unit | Labels | Назначение |
|---|---|---|---|---|
| `authn_jwt_verifications_total` | Counter | — | `result` | Исход разбора JWT в `HS256JWT.parse`/`parse_access`/`parse_refresh`. |
| `authn_password_hash_duration_seconds` | Histogram | `s` | — | Время одного `Argon2idHasher.hash`. |
| `authn_password_verify_duration_seconds` | Histogram | `s` | — | Время одного `Argon2idHasher.verify` или `verify_dummy`. |
| `authn_totp_verifications_total` | Counter | — | `result` | Исход `TOTP.verify`/`verify_once`. |

Границы бакетов обеих гистограмм — общие для репозитория
`otelkit.DURATION_BUCKETS` (`0.005 … 30` с): дефолт OTel SDK рассчитан на
миллисекунды и сложил бы все измерения кита в первый bucket.

### Значения label `result`

`authn_jwt_verifications_total`:

| `result` | Когда |
|---|---|
| `ok` | токен валиден, подпись, сроки и (если запрошен) тип прошли |
| `expired` | сработал `exp` с учётом leeway (`TokenExpiredError`) |
| `signature_invalid` | подпись не сошлась (`TokenSignatureError`) |
| `wrong_type` | `parse_access`/`parse_refresh` получили токен другого типа (`WrongTokenTypeError`) |
| `audience_mismatch` | не совпал ожидаемый `iss`/`aud` (`TokenAudienceError`) |
| `malformed` | битый токен / не-HS256 alg / нет `exp` / `nbf`,`iat` в будущем / payload не объект (`TokenMalformedError`) |

`authn_totp_verifications_total`:

| `result` | Когда |
|---|---|
| `ok` | код принят |
| `invalid` | код отвергнут |
| `secret_invalid` | секрет не читается как base32 (порча данных, не ошибка ввода) — `InvalidSecretError` |
| `replay` | `verify_once`: код уже был принят в своём окне |
| `store_error` | `verify_once`: `UsedCodeStore` недоступен — `UsedCodeStoreError` |

Значение `result` ошибочного исхода лежит на самом исключении
(`TokenError.result`), поэтому метрика и таксономия ошибок не разъезжаются.

## Что эмитится, а что нет

- `hash`, `verify` и `verify_dummy` пишут гистограмму **на каждый вызов**,
  независимо от исхода (включая нечитаемую PHC-строку). Метки исхода у них нет —
  гистограмма меряет только время.
- `authn_jwt_verifications_total` инкрементится **только при разборе**; `sign*`
  метрик не пишет.
- `authn_totp_verifications_total` инкрементится ровно один раз на вызов
  `verify`/`verify_once`; `generate` метрик не пишет.
- RBAC (`Registry`) не эмитит ничего.
- Ни секрет, ни пароль, ни код, ни claims токена в метрики не попадают: все
  label'ы кита — конечные множества исходов.

## Инициализация

Инструменты создаются в конструкторах (`Argon2idHasher`, `HS256JWT`, `TOTP`)
через глобальный провайдер OTel; повторные конструкторы получают те же
инструменты (SDK дедуплицирует по имени, описанию и unit). Глобальный провайдер
делегирующий: `set_meter_provider` после конструктора тоже подхватывает уже
созданные инструменты. Без провайдера — no-op.

Resource attrs (`service.name` и пр.) несёт OTel-resource, настроенный
потребителем в его проводке OTel SDK; в самом ките их выставлять не нужно.
