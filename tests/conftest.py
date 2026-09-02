import sqlite3

import pytest


@pytest.fixture
def fixture_db(tmp_path):
    """小型学校库：两张表，用于工具与图的测试。"""
    p = tmp_path / "school.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, grade INTEGER);
        CREATE TABLE scores (student_id INTEGER, subject TEXT, score REAL);
        INSERT INTO students VALUES (1, 'Alice', 3), (2, 'Bob', 2);
        INSERT INTO scores VALUES (1, 'math', 95.5), (2, 'math', 88.0), (1, 'english', 90.0);
        """
    )
    conn.commit()
    conn.close()
    return str(p)


@pytest.fixture
def fixture_conn(fixture_db):
    conn = sqlite3.connect(f"file:{fixture_db}?mode=ro", uri=True)
    yield conn
    conn.close()


def make_fixture_db(path_dir):
    """给评测测试用：在目录下建 school.sqlite，返回其路径字符串。"""
    import sqlite3
    from pathlib import Path

    p = Path(path_dir) / "school.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, grade INTEGER);
        INSERT INTO students VALUES (1, 'Alice', 3), (2, 'Bob', 2);
        """
    )
    conn.commit()
    conn.close()
    return str(p)
