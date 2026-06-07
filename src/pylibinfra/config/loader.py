"""config — композиция источников конфигурации.

env читается нативно pydantic-settings (базовый слой), Source'ы накладываются
поверх как override'ы (типично Vault): load() собирает их значения в init-kwargs
модели, а init-kwargs в pydantic-settings приоритетнее env — поэтому Vault
перезатирает env-default'ы (как в golibinfra: env-defaults → vault-override).
"""

from typing import Any, Protocol

from pydantic import BaseModel, ValidationError


class Source(Protocol):
    def apply(self, dst: dict[str, Any]) -> None: ...


def load[T: BaseModel](model_cls: type[T], *sources: Source) -> T:
    overrides: dict[str, Any] = {}
    for i, src in enumerate(sources):
        try:
            src.apply(overrides)
        except Exception as exc:
            raise RuntimeError(f"config: source[{i}]: {exc}") from exc
    try:
        return model_cls(**overrides)
    except ValidationError as exc:
        # str(ValidationError)/трейсбек печатает input_value — секреты утекли бы;
        # перечисляем только локации упавших полей, без input/ctx
        fields = ", ".join(".".join(str(p) for p in e["loc"]) or "<root>" for e in exc.errors())
        raise RuntimeError(
            f"config: invalid {model_cls.__name__}: {exc.error_count()} validation error(s) "
            f"in field(s): {fields}"
        ) from None
