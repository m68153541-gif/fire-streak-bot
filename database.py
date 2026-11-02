import sqlite3
from datetime import datetime, timedelta
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
    # Таблица серий
    c.execute("""
        CREATE TABLE IF NOT EXISTS streaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE NOT NULL,
            connection_id_a TEXT,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            streak INTEGER DEFAULT 1,
            is_gray INTEGER DEFAULT 0,
            gray_since TEXT,
            restore_count INTEGER DEFAULT 0,
            active_msg_id INTEGER,
            user_a_pressed INTEGER DEFAULT 0,
            user_b_pressed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Таблица подключений Business
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_connections (
            user_id INTEGER PRIMARY KEY,
            connection_id TEXT NOT NULL
        )
    """)
    # Таблица кастомизации огонька
    c.execute("""
        CREATE TABLE IF NOT EXISTS fire_customization (
            user_id INTEGER,
            partner_id INTEGER,
            fire_name TEXT DEFAULT 'Огонек',
            hat TEXT DEFAULT 'none',
            scarf TEXT DEFAULT 'none',
            shirt TEXT DEFAULT 'none',
            PRIMARY KEY (user_id, partner_id)
        )
    """)
    conn.commit()
    conn.close()

def save_connection(user_id, connection_id):
    conn = get_conn()
    conn.execute("""
        INSERT INTO user_connections (user_id, connection_id)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET connection_id = excluded.connection_id
    """, (user_id, connection_id))
    conn.commit()
    conn.close()

def get_connection(user_id):
    conn = get_conn()
    row = conn.execute("SELECT connection_id FROM user_connections WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["connection_id"] if row else None

def create_streak(chat_id, conn_id, user_a, user_b):
    conn = get_conn()
    conn.execute("""
        INSERT INTO streaks (chat_id, connection_id_a, user_a, user_b, streak, is_gray)
        VALUES (?, ?, ?, ?, 1, 0)
        ON CONFLICT(chat_id) DO UPDATE SET
            connection_id_a = excluded.connection_id_a,
            user_a = excluded.user_a,
            user_b = excluded.user_b,
            streak = 1,
            is_gray = 0
    """, (chat_id, conn_id, user_a, user_b))
    conn.commit()
    conn.close()

def get_streak(chat_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM streaks WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row

def get_all_streaks():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM streaks").fetchall()
    conn.close()
    return rows

# --- НОВЫЕ ФУНКЦИИ ДЛЯ MINI APP ---

def get_fire_data(user_id, partner_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM fire_customization WHERE user_id = ? AND partner_id = ?",
        (user_id, partner_id)
    ).fetchone()
    conn.close()
    if not row:
        return {"fire_name": "Огонек", "hat": "none", "scarf": "none", "shirt": "none"}
    return dict(row)

def save_fire_data(user_id, partner_id, fire_name, hat, scarf, shirt):
    conn = get_conn()
    conn.execute("""
        INSERT INTO fire_customization (user_id, partner_id, fire_name, hat, scarf, shirt)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, partner_id) DO UPDATE SET
            fire_name = excluded.fire_name,
            hat = excluded.hat,
            scarf = excluded.scarf,
            shirt = excluded.shirt
    """, (user_id, partner_id, fire_name, hat, scarf, shirt))
    conn.commit()
    conn.close()

def get_partners(user_id):
    """Возвращает список ID собеседников, с которыми есть серия."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_a, user_b FROM streaks WHERE user_a = ? OR user_b = ?",
        (user_id, user_id)
    ).fetchall()
    conn.close()
    
    partners = []
    for row in rows:
        partner = row["user_b"] if row["user_a"] == user_id else row["user_a"]
        if partner not in partners:
            partners.append(partner)
    return partners
