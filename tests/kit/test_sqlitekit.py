"""共用的 SQLite 基礎:WAL、忙碌逾時、BEGIN IMMEDIATE、忙碌與永久故障要分得開。"""

import sqlite3

import pytest

from rtb.sqlitekit import DatabaseBusy, begin_immediate, connect, immediate_transaction

SCHEMA = "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT);"


def test_connect_enables_wal_creates_the_schema_and_uses_manual_transactions(tmp_path):
    conn = connect(tmp_path / "x.db", schema=SCHEMA)

    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.execute("INSERT INTO t (v) VALUES ('a')")
    assert not conn.in_transaction  # isolation_level=None:沒有悄悄開啟的交易
    conn.close()


def test_a_second_writer_gets_database_busy_after_the_timeout(tmp_path):
    first = connect(tmp_path / "x.db", schema=SCHEMA)
    second = connect(tmp_path / "x.db", busy_timeout_seconds=0.05)
    begin_immediate(first)

    with pytest.raises(DatabaseBusy):
        begin_immediate(second)

    first.execute("ROLLBACK")
    begin_immediate(second)  # 鎖放掉之後就能進
    second.execute("ROLLBACK")
    first.close()
    second.close()


def test_a_failure_that_is_not_lock_contention_is_not_disguised_as_busy(tmp_path):
    conn = connect(tmp_path / "x.db", schema=SCHEMA)
    begin_immediate(conn)

    with pytest.raises(sqlite3.OperationalError) as caught:
        begin_immediate(conn)  # 已經在交易裡:這種錯誤重試也不會好,不能當成「忙碌」

    assert not isinstance(caught.value, DatabaseBusy)
    conn.execute("ROLLBACK")
    conn.close()


def test_a_database_that_cannot_be_opened_raises_the_original_error(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "missing-dir" / "x.db", schema=SCHEMA)


def test_a_failing_schema_leaves_no_open_connection(tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    with pytest.raises(sqlite3.Error):
        connect(tmp_path / "x.db", schema="THIS IS NOT SQL;")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):  # 已關閉的連線不能再用
        opened[0].execute("SELECT 1")


def test_lock_contention_while_initialising_the_connection_is_database_busy(tmp_path):
    path = tmp_path / "x.db"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE t (id INTEGER)")  # 還是舊式日誌模式,切換成 WAL 需要獨佔鎖
    holder.execute("BEGIN EXCLUSIVE")

    with pytest.raises(DatabaseBusy):
        connect(path, busy_timeout_seconds=0.05, schema=SCHEMA)

    holder.execute("ROLLBACK")
    holder.close()
    connect(path, schema=SCHEMA).close()  # 鎖放掉之後可以正常開


def test_an_immediate_transaction_commits_on_success_and_rolls_back_on_any_error(tmp_path):
    conn = connect(tmp_path / "x.db", schema=SCHEMA)

    with immediate_transaction(conn):
        conn.execute("INSERT INTO t (v) VALUES ('kept')")
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        conn.execute("INSERT INTO t (v) VALUES ('lost')")
        raise RuntimeError

    assert conn.execute("SELECT v FROM t").fetchall() == [("kept",)]
    assert not conn.in_transaction
    conn.close()


def test_an_immediate_transaction_reports_lock_contention_as_database_busy(tmp_path):
    first = connect(tmp_path / "x.db", schema=SCHEMA)
    second = connect(tmp_path / "x.db", busy_timeout_seconds=0.05)
    begin_immediate(first)

    with pytest.raises(DatabaseBusy), immediate_transaction(second):
        pass

    first.execute("ROLLBACK")
    first.close()
    second.close()
