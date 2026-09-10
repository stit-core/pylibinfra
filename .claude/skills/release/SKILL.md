---
name: release
description: Выпустить новую версию pylibinfra — make gate-clean, bump версии в pyproject.toml, аннотированный semver-тег vX.Y.Z с release notes + push, затем гайд обновления консьюмера (uv add @vX.Y.Z). Активируй когда юзер говорит "/release", "релизни pylibinfra", "подними версию", "выпусти vX.Y.Z", "затегай новую версию lib'ы".
---

# Skill: release

Версия pylibinfra — поле `version` в `pyproject.toml` + git-тег `vX.Y.Z`. Реестр —
сам Gitea (`git.stit.tech`); отдельного артефакта нет — консьюмеры тянут пакет из
git по тегу. Release notes живут в аннотации тега (`git tag -a`), а не в файле
репозитория.

## Когда применять

- "Релизим vX.Y.Z" / "подними версию pylibinfra" / "/release".
- После закрытой задачи и зелёного gate, когда пользователь сказал «пора».

## Когда НЕ применять

- Локальная разработка с одновременной правкой консьюмера → editable-install
  (`uv add --editable ../pylibinfra`, см. `README.md § Локальная разработка`), не тег.

## Шаги

1. **Gate на чистом дереве.** `make gate-clean` — `make check` в worktree от HEAD
   (ловит незакоммиченные и gitignored-файлы, от которых зависит сборка).
   Красное → не релизим. Для китов с БД/шиной/кэшем/хранилищем дополнительно
   `make test-full` (Docker). `make hooks` держит тот же `make check` на pre-push.
2. **Версия (semver по форме, политика — `README.md § Версионирование`).**
   Совместимость между minor'ами `v1` не гарантируется: **breaking-изменения идут
   minor'ом** (`vX.Y+1.0`) и обязаны быть перечислены в аннотации тега с шагом
   миграции. Только фиксы без смены публичных сигнатур, дефолтов, имён метрик и
   env-alias'ов → patch. Major — отдельное решение оператора, когда потребители
   перестанут обновляться синхронно с библиотекой.
   Список коммитов с прошлого тега:
   `git log $(git describe --tags --abbrev=0)..HEAD --oneline`;
   breaking среди них — `... --oneline | grep '!:'`.
   Спроси у пользователя версию, если неочевидно.
3. **Bump + release notes в аннотации тега.** Обязательные секции: `Breaking`
   (по китам, строкой «что изменилось → как мигрировать»; ни одно breaking не
   пропущено), `Поведение` (сменившиеся дефолты и исходы без смены сигнатур),
   `Изменения` (новое: API, метрики, testkit-сабпакеты, опции), `Гейт` (что
   гонялось). Пуш — гейт человека, делаем по явной просьбе:
   ```bash
   # обновить version = "X.Y.Z" в pyproject.toml, затем:
   uv lock                      # синхронизировать lock с новой версией
   git commit -am "chore: release vX.Y.Z"
   git tag -a vX.Y.Z -F - <<'EOF_NOTES'
   vX.Y.Z

   Breaking:
   - httpkit: map_sentinel → map_is/map_as — замените вызовы
   - ...

   Поведение:
   - natskit: NATS_MAX_RECONNECTS по умолчанию -1 (бесконечные попытки)
   - ...

   Изменения:
   - testkit/logtest: capture-логгер для тестов потребителя
   - ...

   Гейт: make gate-clean, make test-full (Docker).
   EOF_NOTES
   git push origin main vX.Y.Z
   ```
4. **Следующий шаг — консьюмеру.** Напечатай блок обновления для каждого
   Python-шаблона (`telegram-bot-template`, `ai-gateway`/сервисы на pylibinfra)
   вместе с миграционными пунктами из `Breaking` — только теми, что задевают код
   конкретного консьюмера. Сначала сверь baseline консьюмера
   (`grep pylibinfra pyproject.toml`): если он отстаёт больше чем на один тег,
   перечисли цепочку аннотаций (`git tag -l --format='%(contents)' <тег>` на каждую
   промежуточную версию), а не только последний шаг:
   ```bash
   uv add "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@vX.Y.Z"
   uv sync
   make check        # обязательный gate в консьюмере
   ```

## Нет тупика

Скилл всегда заканчивается явным следующим шагом: либо блок `uv add` для консьюмера
с миграционными пунктами, либо (если gate красный) — что чинить перед релизом.
