"""Экранирование MarkdownV2: наборы спецсимволов по контекстам."""

from pylibinfra.tgbotkit import (
    escape_markdown_v2,
    escape_markdown_v2_code,
    escape_markdown_v2_link,
)


def test_escapes_every_reserved_character() -> None:
    for char in "\\_*[]()~`>#+-=|{}.!":
        assert escape_markdown_v2(char) == "\\" + char


def test_keeps_ordinary_text_intact() -> None:
    assert escape_markdown_v2("hub sync degraded") == "hub sync degraded"
    assert escape_markdown_v2("проекция отстаёт") == "проекция отстаёт"


def test_escapes_alert_text_with_operators() -> None:
    # именно такой текст сейчас роняет доставку: '>', '.', '-' зарезервированы
    assert escape_markdown_v2("lag > 300s (task-backend)") == "lag \\> 300s \\(task\\-backend\\)"


def test_code_context_escapes_only_backtick_and_backslash() -> None:
    assert (
        escape_markdown_v2_code("SELECT * FROM t WHERE a < b.c") == "SELECT * FROM t WHERE a < b.c"
    )
    assert escape_markdown_v2_code("`x`") == "\\`x\\`"
    assert escape_markdown_v2_code("a\\b") == "a\\\\b"


def test_link_context_escapes_only_paren_and_backslash() -> None:
    assert escape_markdown_v2_link("https://h.tech/a_b-c.d") == "https://h.tech/a_b-c.d"
    assert escape_markdown_v2_link("https://h.tech/x(1)") == "https://h.tech/x(1\\)"


def test_escaped_text_is_safe_inside_markup() -> None:
    body = escape_markdown_v2("v1.2.3 — deploy failed!")
    assert f"*{body}*" == "*v1\\.2\\.3 — deploy failed\\!*"
