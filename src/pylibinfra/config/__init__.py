"""config — загрузчик App-конфига: ENV-слой pydantic-settings + источники-override'ы.

`load(model, *sources)` применяет все источники и собирает их ошибки в один
`ConfigError`. Кит ничего не эмитит.
"""

from .loader import ConfigError, Source, env_prefixed, load

__all__ = ["ConfigError", "Source", "env_prefixed", "load"]
