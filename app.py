import os
import logging
import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, BusinessConnectionHandler, PreCheckoutQueryHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
URL = os.environ.get("RENDER_EXTERNAL_URL")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
DB_PATH = "streaks.db"
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

# ==================== БАЗА ДАННЫХ ====================

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

def delete_connection(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM user_connections WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

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

# ==================== ХЕНДЛЕРЫ БОТА ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Напиши <code>/l</code>, чтобы проверить статус.\n"
        "3. Напиши <code>/faier</code> в чате с другом.",
        parse_mode="HTML"
    )

async def check_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = get_connection(user.id)
    
    if not conn_id:
        await update.message.reply_text(
            "❌ <b>Бот НЕ подключен к Business.</b>\n\n"
            "Зайди в Настройки → Telegram Business → Чат-боты и добавь меня.",
            parse_mode="HTML"
        )
        return
        
    try:
        conn_info = await context.bot.get_business_connection(conn_id)
        rights = conn_info.rights
        can_reply = rights.can_reply if rights else False
        
        if can_reply:
            await update.message.reply_text(
                "✅ <b>Бот подключен и готов!</b>\n\n"
                "Напиши <code>/faier</code> в чате с другом.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                "⚠️ <b>Нет права отвечать.</b>\n\n"
                "Включи «Отвечать на сообщения» в настройках Business.",
                parse_mode="HTML"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}", parse_mode="HTML")

async def faier_in_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Пытаемся получить сообщение из бизнес-контекста или обычного
    msg = update.business_message or update.message
    if not msg:
        return

    # Получаем conn_id (в бизнес-сообщениях он есть, в обычных может отсутствовать)
    conn_id = getattr(msg, "business_connection_id", None)
    
    # Если это не бизнес-сообщение и нет conn_id — просто выходим
    if not conn_id:
        # Если команда пришла в личку боту, но не в Business — предупреждаем
        if update.effective_chat.type == "private":
             await context.bot.send_message(
                 chat_id=msg.chat.id,
                 text="⚠️ Эта команда работает только в чате с другом (через Telegram Business)."
             )
        return

    chat_id = msg.chat.id

    conn = get_conn()
    row = conn.execute("SELECT user_id FROM user_connections WHERE connection_id = ?", (conn_id,)).fetchone()
    conn.close()
    if not row: 
        return
        
    owner_id = row["user_id"]

    existing = get_streak(chat_id)
    if existing:
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=f"🔥 Серия уже есть: <b>{existing['streak']} дней</b>.",
                parse_mode="HTML", business_connection_id=conn_id
            )
        except: pass
        return

    # Удаляем команду из чата
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except: pass

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{owner_id}_{msg.from_user.id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{owner_id}")]
    ])

    try:
        await context.bot.send_message(
            chat_id=chat_id, text="🔥 <b>Предложение растить огонек!</b>",
            parse_mode="HTML", reply_markup=keyboard, business_connection_id=conn_id
        )
    except Exception as e:
        logger.error(f"Ошибка отправки приглашения: {e}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id, receiver_id = int(parts[1]), int(parts[2])
        chat_id = query.message.chat.id 
        conn_id = get_connection(initiator_id)
        create_streak(chat_id, conn_id, initiator_id, receiver_id)
        await query.edit_message_text("🔥 <b>Серия начата! День 1.</b>", parse_mode="HTML")
        
        try:
            await context.bot.send_message(
                chat_id=chat_id, text="🔥 <b>Огонек зажжен!</b>",
                parse_mode="HTML", business_connection_id=conn_id
            )
        except: pass

    elif data.startswith("dec_"):
        await query.edit_message_text("❌ Отклонено.")

    elif data == "keep_":
        chat_id = query.message.chat.id
        status, result = press_button(chat_id, user.id)
        if status == "waiting" and result == 1:
            await query.answer("✅ Ты поддержал огонек!")
            await query.edit_message_text(
                f"⚪ <b>Ждем второго...</b>\nСтатус: 1/2",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Поддержать (1/2)", callback_data="keep_")]]),
                parse_mode="HTML"
            )
        elif status == "success":
            new_streak = result
            await query.message.delete()
            streak = get_streak(chat_id)
            await context.bot.send_message(
                chat_id=chat_id, text=f"🔥 <b>Серия: {new_streak} дней!</b>",
                parse_mode="HTML", business_connection_id=streak["connection_id_a"]
            )

    elif data.startswith("restore_"):
        chat_id = int(data.split("_")[1])
        streak = get_streak(chat_id)
        if not streak: return
        
        if streak["restore_count"] < 3:
            if restore_streak(chat_id, paid=False):
                await query.edit_message_text("🔥 <b>Огонек восстановлен!</b>", parse_mode="HTML")
        else:
            try:
                await context.bot.send_invoice(
                    chat_id=chat_id,
                    title="Восстановление огонька",
                    description=f"Восстановить серию {streak['streak']} дней за 25 звезд",
                    payload=f"restore_{chat_id}",
                    provider_token="",
                    currency="XTR",
                    prices=[LabeledPrice(label="Восстановление", amount=25)],
                    business_connection_id=streak["connection_id_a"]
                )
            except Exception as e:
                logger.error(f"Ошибка счета: {e}")

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if payment.invoice_payload.startswith("restore_"):
        chat_id = int(payment.invoice_payload.split("_")[1])
        restore_streak(chat_id, paid=True)
        await update.message.reply_text("🔥 <b>Огонек восстановлен за 25 ⭐!</b>", parse_mode="HTML")

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        save_connection(conn.user.id, conn.id)
        logger.info(f"✅ Подключение: {conn.user.id}")
    else:
        delete_connection(conn.user.id)
        logger.info(f"❌ Отключение: {conn.user.id}")

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    for s in get_all_streaks():
        if s["is_gray"] == 1: continue
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать огонек", callback_data="keep_")]])
        try:
            msg = await context.bot.send_message(
                chat_id=s["chat_id"], text="⚪ <b>Огонек стал серым.</b>\n\nНажмите кнопку (нужно обоим):",
                parse_mode="HTML", reply_markup=keyboard, business_connection_id=s["connection_id_a"]
            )
            set_gray(s["chat_id"], msg.message_id)
        except Exception as e:
            logger.error(f"Failed midnight: {e}")

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    for s in get_unsent_reminders():
        if not s["gray_since"]: continue
        if datetime.now(TZ_EKB) - datetime.fromisoformat(s["gray_since"]) > timedelta(days=7):
            kill_streak(s["chat_id"])
            try:
                await context.bot.send_message(chat_id=s["chat_id"], text="💀 <b>Серия окончательно сгорела.</b>", parse_mode="HTML", business_connection_id=s["connection_id_a"])
            except: pass
            continue
        
        if s["restore_count"] < 3:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")],
                [InlineKeyboardButton("♻️ Восстановить", callback_data=f"restore_{s['chat_id']}")]
            ])
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")]])
            
        try:
            await context.bot.send_message(
                chat_id=s["chat_id"], text="⚪ <b>Напоминание:</b> огонек серый. Пора продлить!",
                parse_mode="HTML", reply_markup=keyboard, business_connection_id=s["connection_id_a"]
            )
        except Exception as e:
            logger.error(f"Failed remind: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

# ==================== ЗАПУСК ====================

def main():
    if not TOKEN or not URL:
        logger.error("Не заданы TOKEN или URL")
        return
        
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("l", check_connection))
    
    # Ловим бизнес-сообщения
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE & filters.COMMAND, faier_in_chat))
    # Ловим команду /faier как обычную (на случай, если Telegram пришлет её вне бизнес-контекста)
    app.add_handler(CommandHandler("faier", faier_in_chat))
    
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(BusinessConnectionHandler(on_business_connect))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_error_handler(error_handler)

    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    logger.info(f"Starting webhook on {URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}",
        drop_pending_updates=True,
        allowed_updates=[
            "message",
            "callback_query",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages"
        ]
    )

if __name__ == "__main__":
    main()
