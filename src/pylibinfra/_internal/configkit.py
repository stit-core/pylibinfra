"""Единый контракт конфигов китов.

`KitSettings` — база конфигов: общий `model_config` и строгий ENV-источник, который
читает только `validation_alias` полей; `settings_config` — тот же `model_config`
для подклассов с переопределениями; `ConfigError` — база для `<kit>.ConfigError`;
`build` строит конфиг и переводит ошибку валидации (и нечитаемую переменную) в
ошибку кита, не раскрывая значений полей (DSN, токены).
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)


class ConfigError(ValueError):
    """База невалидного конфига; каждый кит объявляет подкласс `<kit>.ConfigError`."""


def settings_config(**overrides: Any) -> SettingsConfigDict:
    """Общий `model_config` конфигов китов.

    extra=ignore — чужие переменные окружения не мешают; populate_by_name — поле
    заполняется в конструкторе и по имени, и по env-alias'у; env_ignore_empty —
    заданная, но пустая переменная читается как незаданная и берёт дефолт;
    frozen — конфиг неизменяем после валидации.
    """
    cfg: dict[str, Any] = {
        "extra": "ignore",
        "populate_by_name": True,
        "env_ignore_empty": True,
        "frozen": True,
    }
    cfg.update(overrides)
    return cast("SettingsConfigDict", cfg)


class StrictEnvSettingsSource(EnvSettingsSource):
    """ENV-источник, читающий поле только по его `validation_alias`.

    pydantic-settings ищет переменную и по имени поля: `PATH` уезжал бы в поле
    `path`, `MAX_CONNS` — в `max_conns` мимо `PG_MAX_CONNS`, `SECRET` — в поле
    секрета вебхука, `CORS` — во вложенную модель (и валил бы разбор JSON).
    Alias — единственный ENV-контракт кита, поэтому поле без alias'а из окружения
    не заполняется вовсе: его значение задаёт caller (обычно `default_factory`,
    который сам читает ENV по своим алиасам).
    """

    def _extract_field_info(self, field: FieldInfo, field_name: str) -> list[tuple[str, str, bool]]:
        infos = super()._extract_field_info(field, field_name)
        # Запись «по имени поля» pydantic-settings добавляет последней; alias'ные
        # идут перед ней, поэтому срезаем ровно её, а не всё с таким field_key.
        if (
            infos
            and infos[-1][0] == field_name
            and infos[-1][1] == self._field_name_env(field_name)
        ):
            infos = infos[:-1]
        return infos

    def _get_resolved_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Пустой список ENV-имён (поле без alias'а) базовый класс не переживает:
        # он читает field_infos[0] и возвращает переменные цикла по ним.
        if not self._extract_field_info(field, field_name):
            return None, field_name, False
        return super()._get_resolved_field_value(field, field_name)

    def _field_name_env(self, field_name: str) -> str:
        prefix = self.env_prefix if self.env_prefix_target in ("variable", "all") else ""
        return self._apply_case_sensitive(prefix + field_name)


class KitSettings(BaseSettings):
    """База конфигов китов: `settings_config()` + строгий ENV-источник."""

    model_config = settings_config()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Источники: аргументы конструктора, затем ENV по alias'ам; .env и secret-файлы — нет."""
        return (init_settings, StrictEnvSettingsSource(settings_cls))


def build[T: BaseModel](model: type[T], kit: str, error: type[ConfigError], **values: Any) -> T:
    """Строит конфиг (`values` приоритетнее ENV); невалидный — `error` без значений полей."""
    try:
        return model(**values)
    except ValidationError as exc:
        raise error(describe(kit, exc)) from None
    except SettingsError as exc:
        # Нечитаемая переменная (сломанный JSON у complex-поля) — тоже невалидный
        # конфиг; причина отбрасывается, в её тексте лежит значение переменной.
        raise error(f"{kit}: invalid config: {exc}") from None


def describe(kit: str, exc: ValidationError) -> str:
    """Текст ошибки конфига: локации полей и сообщения, без input_value."""
    parts: list[str] = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return f"{kit}: invalid config: " + "; ".join(parts)
