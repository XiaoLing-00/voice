# service/db.py
import sqlite3
from threading import Lock


class DatabaseManager:
    _instance = None
    _lock = Lock()

    def __new__(cls, db_path: str = "interview.db"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.conn = sqlite3.connect(
                    db_path, check_same_thread=False
                )
                cls._instance.conn.execute("PRAGMA journal_mode=WAL;")
                cls._instance.conn.execute("PRAGMA synchronous=NORMAL;")
                cls._instance.conn.execute("PRAGMA foreign_keys=ON;")
        return cls._instance

    def execute(self, sql: str, params: tuple = ()):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        self.conn.commit()
        return cur

    def executemany(self, sql: str, params):
        cur = self.conn.cursor()
        cur.executemany(sql, params)
        self.conn.commit()
        return cur

    def fetchall(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params).fetchone()
