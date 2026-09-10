"""Экранирование Telegram MarkdownV2 по контекстам вставки (stdlib-only, без aiogram)."""

from __future__ import annotations

# Bot API § MarkdownV2 плюс сам '\' — иначе хвост строки уедет в экранирование следующего.
_TEXT_TABLE = str.maketrans({c: "\\" + c for c in "\\_*[]()~`>#+-=|{}.!"})
_CODE_TABLE = str.maketrans({c: "\\" + c for c in "\\`"})
_LINK_TABLE = str.maketrans({c: "\\" + c for c in "\\)"})

__all__ = [
    "escape_markdown_v2",
    "escape_markdown_v2_code",
    "escape_markdown_v2_link",
]


def escape_markdown_v2(text: str) -> str:
    """Экранирует динамику для вставки в MarkdownV2 вне кода и ссылок.

    Служебный символ вне разметки Telegram трактует не как текст, а как
    `400 can't parse entities`, поэтому имена, значения метрик и куски логов
    прогоняются через эту функцию до склейки с разметкой. К готовой размеченной
    строке применять нельзя — экранируется и сама разметка.
    """
    return text.translate(_TEXT_TABLE)


def escape_markdown_v2_code(text: str) -> str:
    """Экранирует содержимое `inline code` и ```pre```: там служебны только ` и \\."""
    return text.translate(_CODE_TABLE)


def escape_markdown_v2_link(url: str) -> str:
    """Экранирует URL внутри `(...)`: там служебны только ) и \\."""
    return url.translate(_LINK_TABLE)
