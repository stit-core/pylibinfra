UV      ?= uv run --no-sync
PYTEST  ?= $(UV) pytest -p no:cacheprovider

.DEFAULT_GOAL := help

.PHONY: help sync check plan-check fmt fmt-check lint typecheck test test-full coverage envdoc-check vuln gate-clean hooks

help: ## Список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

sync: ## Установить deps + dev-инструменты (uv sync --frozen)
	uv sync --frozen

check: fmt-check lint typecheck test envdoc-check plan-check ## Канонический gate: fmt+lint+types+unit+envdoc+plans (перед тегом)

fmt: ## Форматирование (ruff format)
	$(UV) ruff format .

fmt-check: ## Проверка форматирования без записи
	$(UV) ruff format --check .

lint: ## ruff check (pyproject.toml)
	$(UV) ruff check .

typecheck: ## mypy strict по библиотечному коду
	$(UV) mypy src

test: ## Unit-тесты без контейнеров (≈ go test -short)
	$(PYTEST) -m "not integration" -q

test-full: ## Все тесты, включая testcontainers (нужен Docker)
	$(PYTEST) -q

coverage: ## Покрытие полного прогона + сводка по пакетам
	$(PYTEST) -q --cov=pylibinfra --cov-report=term-missing

plan-check: ## Формат планов .claude/plans/ (A=--summary — сводка активных планов)
	bash scripts/validate-plan.sh $(A)

envdoc-check: ## validation_alias ↔ README китов и корневая env-таблица
	scripts/envdoc-check.sh

vuln: ## Известные уязвимости в графе зависимостей (uvx pip-audit; нужна сеть)
	uv export --frozen --no-hashes --all-extras --no-emit-project --format requirements-txt -o /tmp/pylibinfra-requirements.txt
	uvx pip-audit --strict -r /tmp/pylibinfra-requirements.txt

gate-clean: ## `make check` в чистом worktree HEAD (ловит gitignored-файлы)
	scripts/gate-clean.sh

hooks: ## Включить .githooks (pre-push = make check)
	git config core.hooksPath .githooks
