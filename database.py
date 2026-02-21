import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "ipsw_bot.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                chat_id       INTEGER NOT NULL,
                device_identifier TEXT NOT NULL,
                device_name   TEXT NOT NULL,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, device_identifier)
            );

            CREATE TABLE IF NOT EXISTS firmware_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                device_identifier TEXT NOT NULL,
                source        TEXT NOT NULL,
                snapshot      TEXT NOT NULL,
                updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_identifier, source)
            );
        """)


# ---------- subscriptions ----------

def add_subscription(user_id: int, chat_id: int, device_identifier: str, device_name: str):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO subscriptions
                (user_id, chat_id, device_identifier, device_name)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, chat_id, device_identifier, device_name),
        )


def remove_subscription(user_id: int, device_identifier: str):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND device_identifier = ?",
            (user_id, device_identifier),
        )


def get_user_subscriptions(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchall()


def get_all_subscriptions():
    with get_connection() as conn:
        return conn.execute("SELECT * FROM subscriptions").fetchall()


def get_subscriptions_for_device(device_identifier: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM subscriptions WHERE device_identifier = ?",
            (device_identifier,),
        ).fetchall()


def is_subscribed(user_id: int, device_identifier: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id = ? AND device_identifier = ?",
            (user_id, device_identifier),
        ).fetchone()
        return row is not None


# ---------- firmware snapshots ----------

def get_snapshot(device_identifier: str, source: str):
    """Return parsed list of {version, buildid, signed} or None if not cached."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot FROM firmware_snapshots WHERE device_identifier = ? AND source = ?",
            (device_identifier, source),
        ).fetchone()
        if row:
            return json.loads(row["snapshot"])
    return None


def save_snapshot(device_identifier: str, source: str, firmwares: list):
    data = json.dumps(firmwares)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO firmware_snapshots
                (device_identifier, source, snapshot, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (device_identifier, source, data),
        )
