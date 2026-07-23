"""config — загрузчик env (pydantic-settings) + композиция Source-override'ов."""

from .loader import Source, load

__all__ = [
    "Source",
    "load",
]
