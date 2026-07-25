"""markdown.py — экранирование Telegram MarkdownV2. PURE (stdlib-only).

Telegram парсит MarkdownV2 строго: служебный символ вне разметки — не «текст как
есть», а `400 can't parse entities`. Для фоновой доставки это дороже, чем кривое
оформление: 400 → nak → исчерпание max_deliver → term, то есть сообщение
пропадает молча. Поэтому любую динамику (имена, значения метрик, куски логов)
паблишер прогоняет через escape_markdown_v2 перед склейкой с разметкой.

Наборы разные по контекстам (Bot API § MarkdownV2 style), отсюда три функции:
обычный текст экранирует весь набор, содержимое `code`/```pre``` — только backtick
и backslash, URL внутри `(...)` — закрывающую скобку и backslash. Применять к
готовой размеченной строке нельзя — экранируется и сама разметка.
"""

from __future__ import annotations

# Bot API: '_' '*' '[' ']' '(' ')' '~' '`' '>' '#' '+' '-' '=' '|' '{' '}' '.' '!'
# плюс сам '\' — иначе хвост строки уедет в экранирование следующего символа.
_TEXT_TABLE = str.maketrans({c: "\\" + c for c in "\\_*[]()~`>#+-=|{}.!"})
_CODE_TABLE = str.maketrans({c: "\\" + c for c in "\\`"})
_LINK_TABLE = str.maketrans({c: "\\" + c for c in "\\)"})


def escape_markdown_v2(text: str) -> str:
    """Экранирует текст для вставки в MarkdownV2-сообщение (вне code и ссылок)."""
    return text.translate(_TEXT_TABLE)


def escape_markdown_v2_code(text: str) -> str:
    """Экранирует содержимое `inline code` / ```pre```: там служебны только ` и \\."""
    return text.translate(_CODE_TABLE)


def escape_markdown_v2_link(url: str) -> str:
    """Экранирует URL внутри `(...)`: там служебны только ) и \\."""
    return url.translate(_LINK_TABLE)


__all__ = [
    "escape_markdown_v2",
    "escape_markdown_v2_code",
    "escape_markdown_v2_link",
]
