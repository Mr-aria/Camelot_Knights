import sqlite3
import json
from datetime import datetime
from config import DB_NAME

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        is_blocked INTEGER DEFAULT 0,
        first_seen TEXT,
        last_seen TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        user_id INTEGER,
        details TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message_type TEXT,
        content TEXT,
        file_id TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_enabled', '1')
    """)

    conn.commit()
    conn.close()

def set_setting(key, value):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO settings (key, value)
    VALUES (?, ?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

def is_bot_enabled():
    return get_setting("bot_enabled", "1") == "1"

def set_bot_enabled(enabled: bool):
    set_setting("bot_enabled", "1" if enabled else "0")

def upsert_user(user_id, username, full_name):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = cur.fetchone()

    if exists:
        cur.execute("""
        UPDATE users
        SET username = ?, full_name = ?, last_seen = ?
        WHERE user_id = ?
        """, (username, full_name, now, user_id))
    else:
        cur.execute("""
        INSERT INTO users (user_id, username, full_name, is_blocked, first_seen, last_seen)
        VALUES (?, ?, ?, 0, ?, ?)
        """, (user_id, username, full_name, now, now))

    conn.commit()
    conn.close()

def get_user(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def block_user(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_user_blocked(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row["is_blocked"]) if row else False

def add_log(action, user_id=None, details=None):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
    INSERT INTO logs (action, user_id, details, created_at)
    VALUES (?, ?, ?, ?)
    """, (action, user_id, details, now))
    conn.commit()
    conn.close()

def add_report(user_id, message_type, content=None, file_id=None):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
    INSERT INTO reports (user_id, message_type, content, file_id, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (user_id, message_type, content, file_id, now))
    conn.commit()
    conn.close()

def get_recent_logs(limit=20):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT * FROM logs
    ORDER BY id DESC
    LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def export_backup():
    conn = get_connection()
    cur = conn.cursor()

    data = {}
    for table in ["settings", "users", "logs", "reports"]:
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        data[table] = [dict(r) for r in rows]

    conn.close()
    return data

def restore_backup(data):
    conn = get_connection()
    cur = conn.cursor()

    for table in ["settings", "users", "logs", "reports"]:
        cur.execute(f"DELETE FROM {table}")

    for row in data.get("settings", []):
        cur.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (
            row["key"], row["value"]
        ))

    for row in data.get("users", []):
        cur.execute("""
        INSERT INTO users (user_id, username, full_name, is_blocked, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            row["user_id"], row["username"], row["full_name"],
            row["is_blocked"], row["first_seen"], row["last_seen"]
        ))

    for row in data.get("logs", []):
        cur.execute("""
        INSERT INTO logs (id, action, user_id, details, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (
            row["id"], row["action"], row["user_id"],
            row["details"], row["created_at"]
        ))

    for row in data.get("reports", []):
        cur.execute("""
        INSERT INTO reports (id, user_id, message_type, content, file_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            row["id"], row["user_id"], row["message_type"],
            row["content"], row["file_id"], row["created_at"]
        ))

    conn.commit()
    conn.close()