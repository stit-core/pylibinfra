"""pgtest — фейки `pgkit.Transactor` для тестов домена без Postgres.

`Identity`/`Recorder` кладут маркер `pgkit.with_test_tx`, поэтому код с гейтом на
`pgkit.in_tx` проходит и без базы; `in_rollback_tx` прогоняет тело в реальной
транзакции и откатывает её.
"""

from .tx import Identity, Recorder, in_rollback_tx

__all__ = ["Identity", "Recorder", "in_rollback_tx"]
