import sqlite3

import pytest

from qadata.tools.db import as_uri, open_readonly
from qadata.types import SqlExecutionError


def test_as_uri_plain_posix():
    assert as_uri("/home/u/db.sqlite") == "file:/home/u/db.sqlite?mode=ro"


def test_as_uri_windows_drive():
    assert as_uri("D:/github/agent/data/x.sqlite") == "file:///D:/github/agent/data/x.sqlite?mode=ro"


def test_as_uri_escapes_space_and_chinese():
    uri = as_uri("D:/da ta/库.sqlite")
    assert " " not in uri and "库" not in uri
    assert "%20" in uri  # 空格已转义


def test_open_readonly_can_select(fixture_db):
    conn = open_readonly(fixture_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 2
    finally:
        conn.close()


def test_open_readonly_blocks_write(fixture_db):
    conn = open_readonly(fixture_db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO students VALUES (9, 'Eve', 1)")
    finally:
        conn.close()


def test_open_readonly_missing_db_readable_error(tmp_path):
    with pytest.raises(SqlExecutionError, match="无法打开数据库"):
        open_readonly(str(tmp_path / "no" / "missing.sqlite"))
