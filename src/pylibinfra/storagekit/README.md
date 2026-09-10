# storagekit

S3-совместимое объектное хранилище: порт `Storage` (`put`/`get`/`stat`/`delete`),
presigned-ссылки (`Presigner`), построитель публичного URL (`URLBuilder`),
генератор ключей `new_key` и две реализации — боевая (подпакет `s3` поверх
aioboto3) и in-memory (для тестов потребителя).

Кит доменно-нейтрален: стримит байты как есть. Image/file-семантика — валидация
mime, ограничение размера, декод картинки — на стороне приложения.

Версия и установка — корневой [`README.md`](../../../README.md).

## Импорт

```python
from pylibinfra import storagekit
from pylibinfra.storagekit import s3  # боевой адаптер
```

## Быстрый старт

`StoragekitProvider` даёт `URLBuilder` из `Config`. Боевые `Storage` и
`Presigner` регистрирует `s3.S3Provider`; ему нужны `Config`, `URLBuilder` и
`logkit.Logger` из графа. `Config` потребитель регистрирует сам (та же
асимметрия, что у `cachekit`/`pgkit`/`natskit`).

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit, logkit, storagekit
from pylibinfra.storagekit import s3


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def storage_config(self) -> storagekit.Config:
        return storagekit.new_config()  # STORAGE_S3_* из ENV; caller провайдит явно

    @provide
    def log(self) -> logkit.Logger:
        return logkit.nop()

    @provide
    def probes(self, client: s3.Client, cfg: storagekit.Config) -> healthkit.Probes:
        return (s3.probe(client, cfg),)  # /ready через httpkit


container = make_async_container(storagekit.StoragekitProvider(), s3.S3Provider(), AppProvider())
storage = await container.get(storagekit.Storage)
presigner = await container.get(storagekit.Presigner)
```

При `STORAGE_S3_PROBE_ON_START=true` (default) `S3Provider` проверяет бакет на
выдаче клиента: неверный endpoint, креды или отсутствующий бакет валят старт
приложения, а не первый запрос. `container.close()` закрывает клиент.

### put / get / stat / delete

```python
ref = await storage.put(body, "image/png", size)
# ref.key — `<uuidv7>.png`; ref.url — публичный стабильный URL; ref.size — реальный размер

reader, info = await storage.get(ref.key)
async with reader:  # закрывает соединение до хранилища
    data = await reader.read()  # или `async for chunk in reader`
_ = info  # ObjectInfo(size, content_type, etag, last_modified)

await storage.stat(ref.key)
await storage.delete(ref.key)  # идемпотентен: отсутствующий ключ — не ошибка
```

`body` — `bytes`, async-итератор байтов или file-like с `read` (в том числе
асинхронным). `size >= 0` — точная длина тела: тело короче или длиннее даёт
`storagekit.SizeMismatchError`, объект не остаётся. `size < 0` — длина
неизвестна: тело стримится до конца, `ref.size` — фактический размер.

```python
try:
    ref = await storage.put(body, "image/png", size)
except storagekit.SizeMismatchError:  # подкласс UploadFailedError
    ...
try:
    info = await storage.stat(key)
except storagekit.NotFoundError:  # подкласс GetFailedError и StatFailedError
    ...
```

### Presigned-ссылки

```python
link = await presigner.presign_get(
    ref.key,
    900.0,
    response_content_disposition='attachment; filename="report.pdf"',
    response_content_type="application/pdf",
)
upload = await presigner.presign_put(f"inbox/{ref.key}", 900.0)
```

Подписывается объект сконфигуренного бакета (`STORAGE_S3_BUCKET`). `ttl` — в
диапазоне [1 s, 7 d], иначе `storagekit.PresignFailedError`; в подпись уезжают
целые секунды (`X-Amz-Expires`), дробная часть отбрасывается. `response_*`
кладут в ссылку headers, которые S3 вернёт при скачивании.

### URLBuilder и new_key

```python
urls = storagekit.URLBuilder("https://cdn.example.com", "media", storagekit.URL_STYLE_PATH)
url = urls.build("2026/07/abc.png")  # https://cdn.example.com/media/2026/07/abc.png

key = storagekit.new_key("image/png")  # "<uuidv7>.png"
```

`build` собирает `<base>/<bucket>/<key>` (`URL_STYLE_PATH`) или `<base>/<key>`
(`URL_STYLE_VIRTUAL` — бакет уже в хосте base URL либо за CDN), схлопывая
хвостовые `/` у base и экранируя каждый сегмент ключа. `new_key` выводит
расширение из content-type: явная таблица частых типов (`image/jpeg`→`.jpg`,
`image/png`, `image/webp`, `image/gif`, `image/svg+xml`, `image/avif`,
`application/pdf`, `application/json`, `application/zip`, `application/xml`,
`audio/mpeg`, `audio/ogg`, `audio/wav`, `audio/webm`, `video/mp4`, `video/webm`,
`text/plain`, `text/csv`, `text/html`), затем `mimetypes` процесса;
`application/octet-stream` и неизвестный тип → ключ без расширения.

### in-memory адаптер

```python
memory = storagekit.MemoryStorage(
    storagekit.URLBuilder("https://cdn.example.com", "media", storagekit.URL_STYLE_PATH)
)
ref = await memory.put(data, "image/png", len(data))
stored = memory.bytes(ref.key)  # тест-хелпер: сохранённые байты (None — ключа нет)
keys = memory.keys()  # тест-хелпер: все ключи
```

`MemoryStorage` реализует `Storage` и `Presigner` и проходит тот же контрактный
набор, что боевой адаптер (см. § Тестирование). Без `URLBuilder`
(`MemoryStorage()`) `ref.url` — сам экранированный ключ.

## Конфигурация

`storagekit.Config` — public API (корневой `README.md` § Стабильность API).
`new_config()` читает окружение и валидирует его: невалидное значение валит старт
`storagekit.ConfigError`, а не первый запрос. `new_config(**values)` перекрывает
окружение — так конфиг собирают тесты и код, у которого адрес хранилища приходит
не из ENV. `MemoryStorage` конфига не требует.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `STORAGE_S3_ENDPOINT` | `endpoint` | `str` | — | `host[:port]` **или** `http(s)://host[:port]` без пути, query и кредов (per-concern endpoint); обязателен |
| `STORAGE_S3_ACCESS_KEY` | `access_key` | `SecretStr` | пусто | Access key при `STORAGE_S3_CREDENTIALS=static`; задаётся вместе с secret |
| `STORAGE_S3_SECRET_KEY` | `secret_key` | `SecretStr` | пусто | Secret key при `static`; оба пустых — анонимный доступ (запрос не подписывается) |
| `STORAGE_S3_CREDENTIALS` | `credentials` | `str` | `static` | `static` — ключи из ENV; `chain` — цепочка botocore (AWS-env → IAM/IRSA) |
| `STORAGE_S3_BUCKET` | `bucket` | `str` | — | Имя бакета; обязателен, без `/`, `\` и пробелов. Часть публичного URL, цель presign и health-пробы |
| `STORAGE_S3_REGION` | `region` | `str` | пусто | Регион бакета для SigV4; пустой подписывается как `us-east-1` |
| `STORAGE_S3_USE_SSL` | `use_ssl` | `bool` | `false` | TLS до endpoint'а, если тот задан без схемы; `http://…` + `true` — ошибка |
| `STORAGE_S3_BUCKET_LOOKUP` | `bucket_lookup` | `str` | `auto` | Адресация бакета: `auto` / `path` / `dns` (virtual-hosted) → `addressing_style` botocore |
| `STORAGE_S3_CREATE_BUCKET` | `create_bucket` | `bool` | `false` | Создавать бакет на старте, если его нет (нужны права `s3:CreateBucket`) |
| `STORAGE_S3_PART_SIZE` | `part_size` | `int`, байты | `16777216` | Размер части multipart: `[5 MiB, 5 GiB]`; он же потолок памяти на `put` |
| `STORAGE_S3_MAX_RETRIES` | `max_retries` | `int` | `3` | Повторы botocore после первой попытки; `>= 0` (`0` — без повторов) |
| `STORAGE_S3_PROBE_TIMEOUT` | `probe_timeout` | `float`, секунды | `5.0` | Дедлайн health-пробы и стартовой проверки; `>= 0` (`0` — только дедлайн вызывающего) |
| `STORAGE_S3_PROBE_ON_START` | `probe_on_start` | `bool` | `true` | Fail-fast проверка бакета при выдаче клиента |
| `STORAGE_S3_URL_STYLE` | `url_style` | `str` | `path` | Форма публичного URL: `path` (`<base>/<bucket>/<key>`) или `virtual` (`<base>/<key>`) |
| `STORAGE_PUBLIC_BASE_URL` | `public_base_url` | `str` | пусто | База публичного URL (CDN или публичный хост бакета); если задан — абсолютный http(s) |

**Публичный URL ≠ endpoint.** `STORAGE_S3_ENDPOINT` — адрес, по которому в S3
ходит сервис (часто внутренний хост кластера). `STORAGE_PUBLIC_BASE_URL` —
адрес, по которому объект читает браузер (CDN или публичный хост бакета); он
попадает в `Ref.url` и ни в один запрос кита. Если бакет входит в хост базы
(CDN, virtual-hosted), ставьте `STORAGE_S3_URL_STYLE=virtual`, иначе бакет
продублируется в пути.

**MinIO — `STORAGE_S3_BUCKET_LOOKUP=path`.** Virtual-hosted адресация требует
wildcard-DNS на домен хранилища; у MinIO из коробки его нет.

## Observability

Боевой адаптер эмитит метрики через `otelkit.meter("storagekit")` (scope
`pylibinfra.storagekit`); без сконфигуренного `MeterProvider` они no-op.

| Метрика | Тип | Labels | Что |
|---|---|---|---|
| `storage_ops_total` | counter | `op` (`put` / `get` / `stat` / `delete` / `presign_get` / `presign_put`), `result` (`ok` / `error`) | операции по исходу |
| `storage_op_duration_seconds` | histogram (s) | `op` | длительность операции; границы — общие `otelkit.DURATION_BUCKETS` (0.005 … 30 s) |

`MemoryStorage` метрик не эмитит. Спанов кит не заводит: HTTP-вызовы botocore не
инструментированы, трассировка операций storage — на стороне доменного кода.
Логов кит пишет два: `storagekit: bucket ready` на успешной стартовой проверке и
`storagekit: rejected upload left parts behind`, если не удалось снять
отвергнутую multipart-загрузку.

Health-проба — `s3.probe(client, cfg)`: `HeadBucket` по `STORAGE_S3_BUCKET` под
именем `storagekit` (`s3.PROBE_NAME`) с дедлайном `STORAGE_S3_PROBE_TIMEOUT`.
Потребитель кладёт её в свой `healthkit.Probes`.

## API

### `Storage`

```python
class Storage(Protocol):
    async def put(self, body: Body, content_type: str, size: int) -> Ref: ...
    async def get(self, key: str) -> tuple[ObjectReader, ObjectInfo]: ...
    async def stat(self, key: str) -> ObjectInfo: ...
    async def delete(self, key: str) -> None: ...
```

`put` генерирует ключ через `new_key` и стримит тело; `get` открывает объект на
чтение (закрывает вызывающий); `stat` отдаёт метаданные; `delete` идемпотентен.
Пустой `key` в `get`/`stat`/`delete` — ошибка; отсутствующий объект в
`get`/`stat` — `NotFoundError`.

### `Ref`, `ObjectInfo` и `ObjectReader`

```python
@dataclass(frozen=True, slots=True)
class Ref:
    key: str  # `<uuidv7>.<ext>`
    url: str  # публичный стабильный URL, не протухает
    content_type: str  # нормализованный: пустой → application/octet-stream
    size: int  # реальный размер сохранённого тела


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    content_type: str
    etag: str
    last_modified: datetime


class ObjectReader:
    async def read(self, n: int = -1) -> bytes: ...  # n < 0 — всё тело
    def __aiter__(self) -> AsyncIterator[bytes]: ...  # чанки как их отдаёт хранилище
    async def aclose(self) -> None: ...  # или `async with reader:`
```

### `Presigner`

```python
class Presigner(Protocol):
    async def presign_get(
        self,
        key: str,
        ttl: float,
        *,
        response_content_disposition: str | None = None,
        response_content_type: str | None = None,
    ) -> str: ...
    async def presign_put(self, key: str, ttl: float) -> str: ...


def validate_presign_ttl(ttl: float) -> None  # [MIN_PRESIGN_TTL, MAX_PRESIGN_TTL] = [1 s, 7 d]
```

### `URLBuilder` и ключи

```python
URLBuilder(base_url: str, bucket: str, style: str = URL_STYLE_PATH).build(key: str) -> str
new_key(content_type: str) -> str
normalize_content_type(content_type: str) -> str  # пустой → DEFAULT_CONTENT_TYPE
```

`key` — object key, а не URL-путь: сегменты экранируются, разделители `/`
сохраняются.

### `MemoryStorage`

```python
MemoryStorage(urls: URLBuilder | None = None)
MemoryStorage.bytes(key: str) -> bytes | None  # тест-хелпер
MemoryStorage.keys() -> list[str]              # тест-хелпер
```

Реализует `Storage` и `Presigner`, не персистентна, безопасна для конкурентных
корутин (критические секции — без `await`).

### Подпакет `s3` (боевой адаптер)

```python
async with s3.new_client(cfg) as client: ...          # aioboto3-клиент, закрывается на выходе
s3.Storage(client, cfg, urls=None, log=None)          # storagekit.Storage + storagekit.Presigner
s3.probe(client, cfg) -> healthkit.Probe              # имя s3.PROBE_NAME == "storagekit"
s3.ensure_bucket(client, cfg, log)                    # стартовые проверки вне DI
s3.S3Provider()                                       # → s3.Client, s3.Storage, Storage, Presigner
```

`s3.Client` остаётся в графе — для операций вне порта (bucket policy, lifecycle,
listing).

### Ошибки

```python
StorageError            # база кита
├── ConfigError         # невалидный Config (ещё и ValueError — как все конфиги китов)
├── UploadFailedError   # put не сохранил объект
│   └── SizeMismatchError
├── GetFailedError      ─┐
├── StatFailedError     ─┴── NotFoundError  (объекта нет; поднимают get и stat)
├── DeleteFailedError
└── PresignFailedError
```

`NotFoundError` — подкласс и `GetFailedError`, и `StatFailedError`, поэтому
`except storagekit.NotFoundError` ловит точечно, а `except
storagekit.GetFailedError` — вместе с прочими отказами операции. `delete`
`NotFoundError` не поднимает: он идемпотентен.

## Тестирование

Потребитель тестирует доменную логику без S3, подменяя `Storage` на
`MemoryStorage`:

```python
memory = storagekit.MemoryStorage()
service = Service(memory)  # Service зависит от storagekit.Storage

ref = await service.upload(body, "image/png")
assert memory.bytes(ref.key) == body
```

Обе реализации проходят один контрактный набор
[`testkit.storagetest`](../testkit/storagetest/): контракт `size`, отказ
источника тела, `NotFoundError`, идемпотентность `delete`, нормализация
content-type, диапазон TTL presign, экранирование ключа.

```python
from pylibinfra.testkit import storagetest


def new_subject() -> storagetest.Subject:
    memory = storagekit.MemoryStorage()
    return storagetest.Subject(memory, memory)


await storagetest.run_contract(new_subject)  # весь контракт
await storagetest.run_case("missing_object", new_subject())  # один кейс (для parametrize)
```

Боевой адаптер тестируется на testcontainers MinIO (`testkit.start_minio`, один
контейнер на модуль): `fixture.config(bucket)` собирает `storagekit.Config` на
живой контейнер, не подхватывая `STORAGE_S3_*` окружения.

```python
async with start_minio() as minio:
    cfg = minio.config("test-bucket")
    async with s3.new_client(cfg) as client:
        await client.create_bucket(Bucket=cfg.bucket)
        storage = s3.Storage(client, cfg, None, logkit.nop())
```

Контейнерные тесты помечены `@pytest.mark.integration` и требуют Docker.

## Инварианты и подводные камни

- **Память на `put` ≈ `part_size`.** Тело целиком не буферизуется: кит держит
  одну часть (по умолчанию 16 MiB) плюс чанк источника. Известный `size <=
  part_size` идёт одним `PutObject` без multipart — граница включающая: ровно
  `part_size` байт ещё один запрос, `part_size + 1` уже multipart. Предел S3 в
  10000 частей ограничивает объект (160 GiB на дефолте).
- **`size >= 0` — точная длина, не подсказка.** Обе реализации отклоняют тело
  короче или длиннее заявленного (`SizeMismatchError`), объект не остаётся:
  однозапросная загрузка отвергается до обращения к S3, multipart — снимается
  `AbortMultipartUpload` до `Complete`, поэтому незавершённая загрузка объектом
  не становится. Детерминированная ошибка клиента не ретраится.
- **Отказ источника тела — тоже `UploadFailedError`.** Исключение из
  async-итератора или `read` оборачивается обеими реализациями (`__cause__` —
  исходное), начатая multipart-загрузка снимается; объект не остаётся.
- **Отмена (`asyncio.CancelledError`) прерывает `put` и проходит наружу как есть** —
  в `StorageError` она не заворачивается: отмена вызывающего не отказ хранилища
  (Python-эквивалент отменённого `ctx` в Go). Начатая multipart-загрузка
  снимается best-effort; если снять не удалось, кит пишет
  `storagekit: rejected upload left parts behind` — подберите такие загрузки
  lifecycle-правилом бакета.
- **Бакет должен существовать.** Кит его не создаёт, если не задан
  `STORAGE_S3_CREATE_BUCKET=true`: приложению обычно не нужны права
  `s3:CreateBucket`, а опечатка в имени должна падать, а не заводить новый бакет.
- **Ключи — uuidv7, не content-addressed.** Каждый `put` даёт новый уникальный
  ключ независимо от содержимого; дедупликации по хэшу нет. uuidv7 k-sortable по
  времени создания.
- **`url` стабилен и публичен.** `Ref.url` не протухает — read-side читает
  напрямую. Доступ к бакету должен это допускать (публичное чтение либо CDN
  перед ним); если чтение закрыто, выдавайте `presign_get`.
- **`delete` идемпотентен.** Повторное удаление и удаление несуществующего ключа
  — успех: S3 не сообщает, был ли объект. Отсутствие объекта различают
  `get`/`stat` (`NotFoundError`).
- **Пустой content-type нормализуется.** `put` с `""` сохраняет объект как
  `application/octet-stream` и возвращает этот же тип в `Ref` — `Ref` не
  расходится с метаданными объекта.
- **Ридер `get` закрывает вызывающий.** Незакрытый `ObjectReader` держит
  соединение до хранилища; `async with reader:` — короткий путь.
- **Ретраи и таймауты.** botocore повторяет запрос `STORAGE_S3_MAX_RETRIES` раз
  (mode `standard`), транспорт — connect 30 s, read 60 s. Подпись — SigV4:
  у произвольного endpoint'а botocore по умолчанию взял бы SigV2, которую
  S3-совместимые хранилища уже не принимают.
- **`Config` регистрирует потребитель.** Ни `StoragekitProvider`, ни
  `s3.S3Provider` не провайдят `Config` — добавьте свой `@provide` с
  `storagekit.new_config()`.
- **Per-concern endpoint.** `STORAGE_S3_ENDPOINT` — отдельный «шов» топологии:
  storage можно развести на отдельный хост, не трогая код (корневой `README.md`
  § Per-concern endpoints).
