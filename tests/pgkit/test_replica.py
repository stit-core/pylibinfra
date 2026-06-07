from pylibinfra.pgkit import DB, ReadWriteDB


def _db() -> DB:
    return DB(object())  # type: ignore[arg-type]


def test_with_replica_routes_reads_and_writes() -> None:
    master = _db()
    replica = _db()
    rw = ReadWriteDB(master, replica)
    assert rw.read() is replica
    assert rw.write() is master


def test_without_replica_reads_go_to_master() -> None:
    master = _db()
    rw = ReadWriteDB(master, None)
    assert rw.read() is master
    assert rw.write() is master
