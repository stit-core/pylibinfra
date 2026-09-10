#!/usr/bin/env bash
# validation_alias китов ↔ документация.
#   1. Каждый env-alias кита упомянут в его README.md или docs/*.md.
#   2. Переменная из доков кита существует в коде (или в allow-list).
#   3. Каждая переменная корневой таблицы § Per-concern endpoints — реальный alias.
#   4. Каждый endpoint-alias кита (*_URL, *_DSN, *_ENDPOINT, *_ADDR, *_TARGET)
#      есть в корневой таблице.
set -u

root=$(git rev-parse --show-toplevel)
cd "$root" || exit 1

readonly KITS_DIR=src/pylibinfra
readonly ENDPOINT_RE='_(URL|DSN|ENDPOINT|ADDR|TARGET)$'
readonly ROOT_TABLE_HEADING='## Per-concern endpoints'

tags_in() {
	# validation_alias из non-test .py под каталогом $1; пусто — не ошибка.
	find "$1" -name '*.py' ! -path '*/tests/*' -exec grep -hoE 'validation_alias="[A-Z][A-Z0-9_]*"' {} + 2>/dev/null |
		sed 's/validation_alias="//; s/"//' | sort -u
}

env_prefixes() {
	# Первые сегменты всех alias'ов китов (PG, HTTP, NATS, …): только такие токены
	# в доках считаются ENV-переменными — константы вроде MAX_ACTION_LEN нет.
	tags_in "$KITS_DIR" | sed 's/_.*//' | sort -u
}

vars_in_docs() {
	# `UPPER_SNAKE`-токены с подчёркиванием из README и docs кита $1 с ENV-префиксом.
	cat "$1"/README.md "$1"/docs/*.md 2>/dev/null |
		grep -oE '`[A-Z][A-Z0-9]*(_[A-Z0-9]+)+`' | tr -d '`' | sort -u |
		grep -E "^($(env_prefixes | paste -sd '|'))_"
}

vars_in_root_table() {
	# Секция корневого README от заголовка таблицы до следующего `## `.
	awk -v h="$ROOT_TABLE_HEADING" '
		index($0, h) == 1 { inside = 1; next }
		inside && /^## / { inside = 0 }
		inside { print }
	' README.md | grep -oE '`[A-Z][A-Z0-9]*(_[A-Z0-9]+)+`' | tr -d '`' | sort -u
}

status=0
allow=$(grep -vE '^\s*(#|$)' scripts/envdoc-allow.txt 2>/dev/null | sort -u)
all_code=$(printf '%s\n%s\n' "$(tags_in "$KITS_DIR")" "$allow" | sed '/^$/d' | sort -u)

for dir in "$KITS_DIR"/*/; do
	kit=${dir%/}
	[ -f "$kit/README.md" ] || continue
	code=$(tags_in "$kit")
	docs=$(vars_in_docs "$kit")

	missing=$(comm -23 <(printf '%s\n' "$code" | sed '/^$/d') <(printf '%s\n' "$docs" | sed '/^$/d') | tr '\n' ' ')
	if [ -n "$missing" ]; then
		echo "envdoc: ${kit#"$KITS_DIR"/}: в коде, но не в доках кита: $missing"
		status=1
	fi

	stale=$(comm -23 <(printf '%s\n' "$docs" | sed '/^$/d') <(printf '%s\n' "$all_code" | sed '/^$/d') | tr '\n' ' ')
	if [ -n "$stale" ]; then
		echo "envdoc: ${kit#"$KITS_DIR"/}: в доках, но не в коде: $stale"
		status=1
	fi
done

table=$(vars_in_root_table)
if [ -z "$table" ]; then
	echo "envdoc: README.md: секция '$ROOT_TABLE_HEADING' не найдена или пуста"
	exit 1
fi

ghost=$(comm -23 <(printf '%s\n' "$table") <(printf '%s\n' "$all_code") | tr '\n' ' ')
if [ -n "$ghost" ]; then
	echo "envdoc: README.md § Per-concern endpoints: переменной нет ни в одном ките: $ghost"
	status=1
fi

endpoints=$(tags_in "$KITS_DIR" | grep -E "$ENDPOINT_RE" | sort -u)
absent=$(comm -23 <(printf '%s\n' "$endpoints") <(printf '%s\n' "$table") | tr '\n' ' ')
if [ -n "$absent" ]; then
	echo "envdoc: README.md § Per-concern endpoints: endpoint-переменная кита не в таблице: $absent"
	status=1
fi

exit $status
