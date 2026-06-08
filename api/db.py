import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("LANTERN_DB", "/var/lib/lantern/lantern.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
