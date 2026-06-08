---
name: release
description: Выпустить новую версию pylibinfra — bump версии в pyproject.toml + semver-тег vX.Y.Z + push, затем гайд обновления консьюмера (uv add @vX.Y.Z). Активируй когда юзер говорит "/release", "релизни pylibinfra", "подними версию", "выпусти vX.Y.Z", "затегай новую версию lib'ы".
---

# Skill: release

Версия pylibinfra — поле `version` в `pyproject.toml` + git-тег `vX.Y.Z`. Реестр —
сам Gitea (`git.stit.tech`); консьюмеры тянут пакет из git по тегу.

## Когда применять

- "Релизим vX.Y.Z" / "подними версию pylibinfra" / "/release".
- После закрытой задачи и зелёного gate, когда пользователь сказал «пора».

## Когда НЕ применять

- Локальная разработка с одновременной правкой консьюмера → editable-install
  (`uv add --editable ../pylibinfra`, см. `README.md § Локальная разработка`), не тег.

## Шаги

1. **Gate зелёный.** `ruff format --check .` + `ruff check .` + `mypy src tests` +
   `pytest` — все. Красное → не релизим.
2. **Версия (semver).** Pre-v1.0.0: **minor может быть breaking**. Breaking change
   (переименование public-полей Config/env-alias'ов, смена сигнатур, изменение корп
   obs-инвариантов) → bump minor (`v0.X.0`); багфикс/добавление без слома → patch
   (`v0.X.Y`). Спроси у пользователя версию, если неочевидно.
3. **Bump + тег + push** (пуш — гейт человека, делаем по явной просьбе):
   ```bash
   # обновить version = "X.Y.Z" в pyproject.toml, затем:
   uv lock                      # синхронизировать lock с новой версией
   git commit -am "chore: release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```
4. **Следующий шаг — консьюмеру.** Напечатай блок обновления:
   ```bash
   uv add "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@vX.Y.Z"
   uv sync
   ```
5. **Если менялись эмитимые сигналы** (метрики/labels/span-attrs) — напомни обновить
   инвентарь observability на стороне control-plane (`corp-infra-template`).

## Нет тупика

Скилл всегда заканчивается явным следующим шагом: либо блок `uv add` для консьюмера,
либо (если gate красный) — что чинить перед релизом.
