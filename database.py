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
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_connections (
            user_id INTEGER PRIMARY KEY,
            connection_id TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS peers (
            owner_id INTEGER,
            peer_id INTEGER,
            chat_id INTEGER,
            PRIMARY KEY (owner_id, peer_id)
        )
    """)
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

def save_peer(owner_id, peer_id, chat_id):
    conn = get_conn()
    conn.execute("""
        INSERT INTO peers (owner_id, peer_id, chat_id) VALUES (?, ?, ?)
        ON CONFLICT(owner_id, peer_id) DO UPDATE SET chat_id = excluded.chat_id
    """, (owner_id, peer_id, chat_id))
    conn.commit()
    conn.close()

def get_chat_id(owner_id, peer_id):
    conn = get_conn()
    row = conn.execute("SELECT chat_id FROM peers WHERE owner_id = ? AND peer_id = ?", (owner_id, peer_id)).fetchone()
    conn.close()
    return row["chat_id"] if row else None

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

def set_gray(chat_id, msg_id=None):
    conn = get_conn()
    now = datetime.now(TZ_EKB).isoformat()
    conn.execute("""
        UPDATE streaks SET is_gray = 1, gray_since = ?, active_msg_id = ?, user_a_pressed = 0, user_b_pressed = 0 
        WHERE chat_id = ?
    """, (now, msg_id, chat_id))
    conn.commit()
    conn.close()

def press_button(chat_id, user_id):
    conn = get_conn()
    row = get_streak(chat_id)
    if not row: 
        conn.close()
        return None, 0

    if user_id == row["user_a"]:
        conn.execute("UPDATE streaks SET user_a_pressed = 1 WHERE chat_id = ?", (chat_id,))
    elif user_id == row["user_b"]:
        conn.execute("UPDATE streaks SET user_b_pressed = 1 WHERE chat_id = ?", (chat_id,))
    
    conn.commit()
    row = get_streak(chat_id)
    total_pressed = row["user_a_pressed"] + row["user_b_pressed"]
    
    if row["user_a_pressed"] == 1 and row["user_b_pressed"] == 1:
        new_streak = row["streak"] + 1
        conn.execute("""
            UPDATE streaks SET streak = ?, is_gray = 0, gray_since = NULL, active_msg_id = NULL, 
            user_a_pressed = 0, user_b_pressed = 0 
            WHERE chat_id = ?
        """, (new_streak, chat_id))
        conn.commit()
        conn.close()
        return "success", new_streak
    
    conn.close()
    return "waiting", total_pressed

def restore_streak(chat_id, paid=False):
    conn = get_conn()
    row = get_streak(chat_id)
    if not row: 
        conn.close()
        return False
    if row["restore_count"] >= 3 and not paid:
        conn.close()
        return False
    conn.execute("""
        UPDATE streaks SET is_gray = 0, gray_since = NULL, restore_count = restore_count + 1,
        active_msg_id = NULL, user_a_pressed = 0, user_b_pressed = 0
        WHERE chat_id = ?
    """, (chat_id,))
    conn.commit()
    conn.close()
    return True

def get_unsent_reminders():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM streaks WHERE is_gray = 1").fetchall()
    conn.close()
    return rows

def kill_streak(chat_id):
    conn = get_conn()
    conn.execute("DELETE FROM streaks WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def get_partners(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT user_a, user_b FROM streaks WHERE user_a = ? OR user_b = ?", (user_id, user_id)).fetchall()
    conn.close()
    partners = []
    for row in rows:
        partner = row["user_b"] if row["user_a"] == user_id else row["user_a"]
        if partner not in partners: partners.append(partner)
    return partners

def get_fire_data(user_id, partner_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM fire_customization WHERE user_id = ? AND partner_id = ?", (user_id, partner_id)).fetchone()
    conn.close()
    if not row: return {"fire_name": "Огонек", "hat": "none", "scarf": "none", "shirt": "none"}
    return dict(row)

def save_fire_data(user_id, partner_id, fire_name, hat, scarf, shirt):
    conn = get_conn()
    conn.execute("""
        INSERT INTO fire_customization (user_id, partner_id, fire_name, hat, scarf, shirt)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, partner_id) DO UPDATE SET
            fire_name = excluded.fire_name, hat = excluded.hat,
            scarf = excluded.scarf, shirt = excluded.shirt
    """, (user_id, partner_id, fire_name, hat, scarf, shirt))
    conn.commit()
    conn.close()
