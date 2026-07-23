---
name: add-kit
description: Добавить новый kit (пакет) в pylibinfra по конвенциям библиотеки — package-doc, dishka Provider, опц. pydantic-settings Config, тесты, testkit-сабпакет, строка в README. Активируй когда юзер говорит "/add-kit", "добавь kit", "новый пакет в pylibinfra", "заведи <name>kit", "нужен переиспользуемый примитив X". Для правки существующего kit'а скилл НЕ нужен — это прямой Edit.
---

# Skill: add-kit

Заводит новый переиспользуемый примитив как отдельный пакет-kit. pylibinfra —
общая lib: сюда едет только то, что не специфично для бизнес-домена (доменное
живёт в сервис-репо).

## Когда применять

- "Нужен переиспользуемый kit для X" / "добавь `<name>kit`" / "/add-kit".
- Примитив (generic util, middleware, клиент, helper) нужен ≥2 сервисам или
  концептуально инфраструктурный.

## Когда НЕ применять

- Правка существующего kit'а → прямой Edit.
- Логика, специфичная для одного бизнес-домена → она в сервис-репо, не сюда.

## Шаги

1. **Слаг.** Имя пакета — `<area>kit` (нижний регистр, без подчёркиваний):
   `cachekit`, `authnkit`. Спроси у пользователя назначение и публичный API, если
   неочевидно.
2. **Каталог пакета** `src/pylibinfra/<kit>/`:
   - `<kit>.py` (или несколько модулей) — реализация + package-doc в `__init__.py`
     (одна-две строки: что это и зачем).
   - `module.py` — `class <Kit>Provider(Provider)` (scope `Scope.APP`) с
     `@provide`-конструкторами; `provides=<Interface>` для биндинга к Protocol.
   - `__init__.py` — реэкспорт публичного API + `__all__`.
   - `tests/<kit>/test_*.py` — тесты (unit; testcontainers — через `testkit`, если нужен I/O).
3. **Конфиг (если есть env-параметры).** `pydantic_settings.BaseSettings` с
   `Field(validation_alias="ENV_NAME")` — это **public API** (имена полей и
   env-alias'ы попадут в `README.md § Стабильность API`). Provider Config **не**
   provide'ит (asymmetry note) — caller регистрирует сам; добавь `new_config()`-фабрику.
4. **testkit-сабпакет (опц.).** Нужны helpers для тестов консьюмера — заведи
   `src/pylibinfra/testkit/<name>test/`, где `<name>` — имя кита **без** суффикса
   `kit` (`cachekit` → `testkit/cachetest/`).
5. **Документация.** Добавь строку в `README.md § Пакеты` (slug + ответственность).
   Без этого kit «невидим».
6. **Gate.** `ruff format --check .` + `ruff check .` + `mypy src tests` +
   `pytest` зелёные перед claim «готово».

## Следующий шаг

После зелёного gate — релиз новой версии через `/release` (consumer'ы подтянут kit
через git-тег `@vX.Y.Z`). Новая сторонняя зависимость в `pyproject.toml` — сперва
согласуй с пользователем.
