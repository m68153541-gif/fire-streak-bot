import sqlite3
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
DB_PATH = "streaks.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS streaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE NOT NULL,
            business_connection_id TEXT,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            streak INTEGER DEFAULT 1,
            last_active_date TEXT,
            is_gray INTEGER DEFAULT 0,
            gray_since TEXT,
            restore_count INTEGER DEFAULT 0,
            user_a_wrote INTEGER DEFAULT 0,
            user_b_wrote INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            from_username TEXT,
            to_user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_connections (
            user_id INTEGER PRIMARY KEY,
            business_connection_id TEXT NOT NULL,
            connected_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_connection(user_id, connection_id):
    conn = get_conn()
    conn.execute("""
        INSERT INTO user_connections (user_id, business_connection_id)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET business_connection_id = excluded.business_connection_id
    """, (user_id, connection_id))
    conn.commit()
    conn.close()

def get_connection(user_id):
    conn = get_conn()
    row = conn.execute("SELECT business_connection_id FROM user_connections WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["business_connection_id"] if row else None

def create_streak(chat_id, connection_id, user_a, user_b):
    conn = get_conn()
    today = datetime.now(TZ_EKB).date().isoformat()
    conn.execute("""
        INSERT INTO streaks (chat_id, business_connection_id, user_a, user_b, streak, last_active_date)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            business_connection_id = excluded.business_connection_id,
            streak = streaks.streak + 1,
            last_active_date = excluded.last_active_date,
            is_gray = 0,
            user_a_wrote = 0,
            user_b_wrote = 0
    """, (chat_id, connection_id, user_a, user_b, today))
    conn.commit()
    conn.close()

def get_streak_by_chat(chat_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM streaks WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row

def mark_user_wrote(chat_id, user_id):
    conn = get_conn()
    row = get_streak_by_chat(chat_id)
    if not row:
        conn.close()
        return
    if user_id == row["user_a"]:
        conn.execute("UPDATE streaks SET user_a_wrote = 1 WHERE chat_id = ?", (chat_id,))
    elif user_id == row["user_b"]:
        conn.execute("UPDATE streaks SET user_b_wrote = 1 WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def try_advance_streak(chat_id):
    """Если оба написали — продлеваем серию и сбрасываем флаги."""
    conn = get_conn()
    row = get_streak_by_chat(chat_id)
    if not row or row["is_gray"] == 1:
        conn.close()
        return None
    if row["user_a_wrote"] == 1 and row["user_b_wrote"] == 1:
        today = datetime.now(TZ_EKB).date().isoformat()
        new_streak = row["streak"] + 1
        conn.execute("""
            UPDATE streaks SET streak = ?, last_active_date = ?, user_a_wrote = 0, user_b_wrote = 0
            WHERE chat_id = ?
        """, (new_streak, today, chat_id))
        conn.commit()
        conn.close()
        return new_streak
    conn.close()
    return None

def set_gray(chat_id):
    conn = get_conn()
    now = datetime.now(TZ_EKB).isoformat()
    conn.execute("UPDATE streaks SET is_gray = 1, gray_since = ? WHERE chat_id = ? AND is_gray = 0", (now, chat_id))
    conn.commit()
    conn.close()

def restore_streak(chat_id, paid=False):
    conn = get_conn()
    row = get_streak_by_chat(chat_id)
    if not row:
        conn.close()
        return False
    if row["restore_count"] >= 3 and not paid:
        conn.close()
        return False
    conn.execute("""
        UPDATE streaks SET is_gray = 0, gray_since = NULL, restore_count = restore_count + 1,
        user_a_wrote = 0, user_b_wrote = 0
        WHERE chat_id = ?
    """, (chat_id,))
    conn.commit()
    conn.close()
    return True

def get_all_streaks():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM streaks").fetchall()
    conn.close()
    return rows

def get_gray_streaks():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM streaks WHERE is_gray = 1").fetchall()
    conn.close()
    return rows

def get_streak_by_users(user_a, user_b):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM streaks WHERE (user_a = ? AND user_b = ?) OR (user_a = ? AND user_b = ?)
    """, (user_a, user_b, user_b, user_a)).fetchone()
    conn.close()
    return row
