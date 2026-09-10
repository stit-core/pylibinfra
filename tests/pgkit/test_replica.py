from pylibinfra.pgkit import DB, ReadWriteDB


def _db() -> DB:
    return DB(object())


def test_with_replica_routes_reads_and_writes() -> None:
    master, replica = _db(), _db()
    rw = ReadWriteDB(master, replica)
    assert rw.read() is replica
    assert rw.write() is master
    assert rw.has_replica() is True


def test_without_replica_reads_go_to_master() -> None:
    master = _db()
    rw = ReadWriteDB(master, None)
    assert rw.read() is master
    assert rw.write() is master
    assert rw.has_replica() is False


def test_same_db_on_both_sides_is_not_a_replica() -> None:
    master = _db()
    rw = ReadWriteDB(master, master)
    assert rw.has_replica() is False
