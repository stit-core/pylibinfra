"""config — композиция источников конфигурации.

env читается нативно pydantic-settings (базовый слой), Source'ы накладываются
поверх как override'ы (типично Vault): load() собирает их значения в init-kwargs
модели, а init-kwargs в pydantic-settings приоритетнее env — поэтому Vault
перезатирает env-default'ы (как в golibinfra: env-defaults → vault-override).
"""

from typing import Any, Protocol

from pydantic import BaseModel


class Source(Protocol):
    def apply(self, dst: dict[str, Any]) -> None: ...


def load[T: BaseModel](model_cls: type[T], *sources: Source) -> T:
    overrides: dict[str, Any] = {}
    for i, src in enumerate(sources):
        try:
            src.apply(overrides)
        except Exception as exc:
            raise RuntimeError(f"config: source[{i}]: {exc}") from exc
    return model_cls(**overrides)
