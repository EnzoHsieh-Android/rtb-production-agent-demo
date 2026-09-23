"""共用資料庫工具的唯讀連線(Phase 9 增量 1):[S609]。

以 SQLite 官方 URI 的 mode=ro 開;前掃實測過把「mode=ro」字串丟給一般的連線函式不會報錯,而是在
磁碟上悄悄新建一個以那串文字為檔名的空資料庫,所以要另寫一支、明講這是 URI。
"""

import sqlite3

import pytest

from rtb.sqlitekit import connect, connect_read_only, read_snapshot


# ---- [S609] ----
def test_the_read_only_connection_really_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "a b#?.db"  # 檔名帶 URI 的保留字元,也要開到同一個檔
    writer = connect(path, schema="CREATE TABLE t (v TEXT); INSERT INTO t VALUES ('x');")
    writer.close()

    reader = connect_read_only(path)
    try:
        assert reader.execute("SELECT v FROM t").fetchall() == [("x",)]
        for statement in ("INSERT INTO t VALUES ('y')", "CREATE TABLE u (v)", "DELETE FROM t",
                          "PRAGMA user_version = 3"):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute(statement)
        with read_snapshot(reader):
            assert reader.in_transaction
            assert reader.execute("SELECT count(*) FROM t").fetchone() == (1,)
        assert not reader.in_transaction
    finally:
        reader.close()

    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    for missing in (tmp_path / "nope.db", tmp_path / "sub" / "nope.db"):
        with pytest.raises(FileNotFoundError):
            connect_read_only(missing)
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # 不存在時不建新檔
