import os
import logging
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, BusinessConnectionHandler, PreCheckoutQueryHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
URL = os.environ.get("RENDER_EXTERNAL_URL")
DATABASE_URL = os.environ.get("DATABASE_URL") 
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
ADMIN_ID = 8513244807
ADMIN_USERNAME = "@fier_zyzabot"

# ==================== БАЗА ДАННЫХ ====================

def get_conn():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, sslmode='require')

def init_db():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS streaks (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT UNIQUE NOT NULL,
                connection_id_a TEXT,
                user_a BIGINT NOT NULL,
                user_b BIGINT NOT NULL,
                streak INTEGER DEFAULT 1,
                is_gray INTEGER DEFAULT 0,
                gray_since TEXT,
                restore_count INTEGER DEFAULT 0,
                active_msg_id BIGINT,
                user_a_pressed INTEGER DEFAULT 0,
                user_b_pressed INTEGER DEFAULT 0,
                total_taps_a INTEGER DEFAULT 0,
                total_taps_b INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_connections (
                user_id BIGINT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                peer_chat_id BIGINT,
                admin_phrase TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка БД: {e}")

def save_connection(user_id, connection_id, peer_chat_id=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO user_connections (user_id, connection_id, peer_chat_id)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET 
            connection_id = EXCLUDED.connection_id,
            peer_chat_id = COALESCE(EXCLUDED.peer_chat_id, user_connections.peer_chat_id)
    """, (user_id, connection_id, peer_chat_id))
    conn.commit()
    conn.close()

def save_peer_chat_id(user_id, peer_chat_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE user_connections SET peer_chat_id = %s WHERE user_id = %s", (peer_chat_id, user_id))
    conn.commit()
    conn.close()

def get_connection(user_id):
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT connection_id FROM user_connections WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["connection_id"] if row else None

def get_peer_chat_id(user_id):
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT peer_chat_id FROM user_connections WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["peer_chat_id"] if row else None

def create_streak(chat_id, conn_id, user_a, user_b):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO streaks (chat_id, connection_id_a, user_a, user_b, streak, is_gray)
        VALUES (%s, %s, %s, %s, 1, 0)
        ON CONFLICT(chat_id) DO UPDATE SET
            connection_id_a = EXCLUDED.connection_id_a,
            user_a = EXCLUDED.user_a,
            user_b = EXCLUDED.user_b,
            streak = 1,
            is_gray = 0
    """, (chat_id, conn_id, user_a, user_b))
    conn.commit()
    conn.close()

def get_streak(chat_id):
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks WHERE chat_id = %s", (chat_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_all_streaks():
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks")
    rows = c.fetchall()
    conn.close()
    return rows

def increment_message_count(chat_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE streaks SET message_count = message_count + 1 WHERE chat_id = %s", (chat_id,))
    conn.commit()
    conn.close()
    return get_streak(chat_id)["message_count"]

def set_gray(chat_id, msg_id=None):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now(TZ_EKB).isoformat()
    c.execute("UPDATE streaks SET is_gray = 1, gray_since = %s, active_msg_id = %s, user_a_pressed = 0, user_b_pressed = 0 WHERE chat_id = %s", (now, msg_id, chat_id))
    conn.commit()
    conn.close()

def press_button(chat_id, user_id):
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks WHERE chat_id = %s", (chat_id,))
    row = c.fetchone()
    if not row: 
        conn.close()
        return None, 0

    if user_id == row["user_a"]:
        c.execute("UPDATE streaks SET user_a_pressed = 1 WHERE chat_id = %s", (chat_id,))
    elif user_id == row["user_b"]:
        c.execute("UPDATE streaks SET user_b_pressed = 1 WHERE chat_id = %s", (chat_id,))
    
    conn.commit()
    c.execute("SELECT * FROM streaks WHERE chat_id = %s", (chat_id,))
    row = c.fetchone()
    total_pressed = row["user_a_pressed"] + row["user_b_pressed"]
    
    if row["user_a_pressed"] == 1 and row["user_b_pressed"] == 1:
        new_streak = row["streak"] + 1
        c.execute("UPDATE streaks SET streak = %s, is_gray = 0, gray_since = NULL, active_msg_id = NULL, user_a_pressed = 0, user_b_pressed = 0 WHERE chat_id = %s", (new_streak, chat_id))
        conn.commit()
        conn.close()
        return "success", new_streak
    
    conn.close()
    return "waiting", total_pressed

def restore_streak(chat_id, paid=False):
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks WHERE chat_id = %s", (chat_id,))
    row = c.fetchone()
    if not row: 
        conn.close()
        return False
    if row["restore_count"] >= 3 and not paid:
        conn.close()
        return False
    c.execute("UPDATE streaks SET is_gray = 0, gray_since = NULL, restore_count = restore_count + 1, active_msg_id = NULL, user_a_pressed = 0, user_b_pressed = 0 WHERE chat_id = %s", (chat_id,))
    conn.commit()
    conn.close()
    return True

def get_unsent_reminders():
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks WHERE is_gray = 1")
    rows = c.fetchall()
    conn.close()
    return rows

def kill_streak(chat_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM streaks WHERE chat_id = %s", (chat_id,))
    conn.commit()
    conn.close()

# ==================== ХЕНДЛЕРЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args and context.args[0].startswith("bizChat"):
        chat_id_str = context.args[0][7:]
        try:
            chat_id = int(chat_id_str)
            save_peer_chat_id(user.id, chat_id)
            await update.message.reply_text(
                f"✅ Я запомнил этот чат (ID: <code>{chat_id}</code>).\n\n"
                "Теперь напиши мне в личку команду <code>/send</code>.",
                parse_mode="HTML"
            )
        except ValueError:
            pass
        return

    # Кнопка для открытия Mini App
    web_app_url = f"{URL}/webapp/index.html"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Открыть моего Огонька", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton("📖 Все команды", callback_data="help")]
    ])
    
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Напиши <code>/send</code> и выбери друга.\n"
        "3. Нажми кнопку ниже, чтобы посмотреть на своего огонька!",
        parse_mode="HTML",
        reply_markup=keyboard
    )

async def com_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 <b>Список всех команд:</b>\n\n"
        "🔥 <code>/start</code> — Главная инструкция\n"
        "📤 <code>/send</code> — Отправить приглашение другу\n"
        "👤 <code>/myfaier</code> — Мои огоньки\n"
        "🔍 <code>/l</code> — Проверить статус подключения\n"
        "⚙️ <code>/com</code> — Этот список команд\n"
        "👑 <code>/admin</code> — Панель администратора\n"
        "👑 <code>/adm</code> — Рассылка от админа"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def myfaier_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks WHERE user_a = %s OR user_b = %s ORDER BY streak DESC", (user.id, user.id))
    streaks = c.fetchall()
    conn.close()
    
    if not streaks:
        await update.message.reply_text("❌ У вас пока нет активных огоньков.")
        return
        
    text = "🔥 <b>Ваши огоньки:</b>\n\n"
    for s in streaks:
        partner_id = s["user_b"] if s["user_a"] == user.id else s["user_a"]
        text += f"ID: {partner_id} — <b>Серия: {s['streak']} дней</b>\n"
            
    await update.message.reply_text(text, parse_mode="HTML")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
        
    streaks = get_all_streaks()
    if not streaks:
        await update.message.reply_text("В базе пока нет огоньков.")
        return
        
    text = "👑 <b>Все огоньки в базе:</b>\n\n"
    for s in streaks:
        text += f"{s['user_a']} ↔️ {s['user_b']} (Серия: {s['streak']}, Сообщений: {s['message_count']})\n"
        
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000], parse_mode="HTML")

async def adm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    
    USER_STATES[user.id] = {"state": "waiting_admin_phrase"}
    await update.message.reply_text("👑 Введите фразу, которую нужно разослать во все чаты:")

async def handle_admin_phrase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = USER_STATES.get(user.id)
    if not state or state.get("state") != "waiting_admin_phrase":
        return
    
    phrase = update.message.text.strip()
    USER_STATES.pop(user.id, None)
    
    streaks = get_all_streaks()
    count = 0
    for s in streaks:
        try:
            await context.bot.send_message(
                chat_id=s["chat_id"],
                text=phrase,
                business_connection_id=s["connection_id_a"]
            )
            count += 1
        except Exception as e:
            logger.error(f"Failed to send admin phrase: {e}")
    
    await update.message.reply_text(f"✅ Фраза отправлена в {count} чатов.")

async def send_invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = get_connection(user.id)
    peer_chat_id = get_peer_chat_id(user.id)
    
    if not conn_id:
        await update.message.reply_text("❌ Бот не подключен. Добавь меня в Business.")
        return
    if not peer_chat_id:
        await update.message.reply_text("❌ Я не знаю ID чата. Сначала нажми кнопку бота в чате с другом.")
        return
    
    existing = get_streak(peer_chat_id)
    if existing:
        await update.message.reply_text(f"🔥 Серия уже есть: {existing['streak']} дней.")
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}_{peer_chat_id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{user.id}")]
    ])
    
    try:
        await context.bot.send_message(
            chat_id=peer_chat_id, text="🔥 <b>Предложение растить огонек!</b>\n\nНажми кнопку ниже:",
            parse_mode="HTML", reply_markup=keyboard, business_connection_id=conn_id
        )
        await update.message.reply_text("✅ Приглашение отправлено в чат с другом!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = get_connection(user.id)
    if not conn_id:
        await update.message.reply_text("❌ Нет подключения.")
        return
    try:
        conn_info = await context.bot.get_business_connection(conn_id)
        text = f"<b>ID подключения:</b> {conn_info.id}\n"
        text += f"<b>user_chat_id:</b> {getattr(conn_info, 'user_chat_id', 'Нет')}\n"
        text += f"<b>can_reply:</b> {conn_info.rights.can_reply if conn_info.rights else 'Нет'}\n"
        text += f"<b>Сохраненный peer_chat_id:</b> {get_peer_chat_id(user.id)}\n"
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ==================== БИЗНЕС-СООБЩЕНИЯ ====================

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg: return

    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT user_id FROM user_connections WHERE connection_id = %s", (msg.business_connection_id,))
    row = c.fetchone()
    conn.close()
    
    if not row: return
    owner_id = row["user_id"]
    
    if msg.from_user.id == owner_id:
        return

    streak = get_streak(msg.chat.id)
    if not streak: return

    count = increment_message_count(msg.chat.id)
    
    # Каждые 20 сообщений - милое сообщение
    if count % 20 == 0:
        phrases = [
            "Вы так мило общаетесь! 💬",
            "Я всё вижу, и мне очень нравится! 👀❤️",
            "Продолжайте, у вас отличная беседа! ✨"
        ]
        phrase = random.choice(phrases)
        
        # Админу пишем чаще
        if streak["user_a"] == ADMIN_ID or streak["user_b"] == ADMIN_ID:
            phrase = "Вы так мило общаетесь! Я аж завидую! 💬🔥"
        
        try:
            await context.bot.send_message(
                chat_id=msg.chat.id,
                text=phrase,
                business_connection_id=msg.business_connection_id
            )
        except: pass

# ==================== КНОПКИ И ПЛАТЕЖИ ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "help":
        await query.message.edit_text(
            "📋 <b>Список всех команд:</b>\n\n"
            "🔥 <code>/start</code> — Главная инструкция\n"
            "📤 <code>/send</code> — Отправить приглашение другу\n"
            "👤 <code>/myfaier</code> — Мои огоньки\n"
            "🔍 <code>/l</code> — Проверить статус подключения\n"
            "⚙️ <code>/com</code> — Этот список команд\n"
            "👑 <code>/admin</code> — Панель администратора\n"
            "👑 <code>/adm</code> — Рассылка от админа",
            parse_mode="HTML"
        )
        return
        
    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id, receiver_id = int(parts[1]), int(parts[2])
        chat_id = query.message.chat.id 
        conn_id = get_connection(initiator_id)
        create_streak(chat_id, conn_id, initiator_id, receiver_id)
        
        try:
            await query.edit_message_text("🔥 <b>Серия начата! День 1.</b>", parse_mode="HTML")
        except: pass
            
        try:
            await context.bot.send_message(
                chat_id=chat_id, text="🔥 <b>Огонек зажжен!</b>",
                parse_mode="HTML", business_connection_id=conn_id
            )
        except: pass

    elif data.startswith("dec_"):
        try:
            await query.edit_message_text("❌ Отклонено.")
        except: pass

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
            
            # Кнопка для Mini App после продления
            web_app_url = f"{URL}/webapp/index.html"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Посмотреть огонька", web_app=WebAppInfo(url=web_app_url))]])
            
            await context.bot.send_message(
                chat_id=chat_id, text=f"🔥 <b>Серия: {new_streak} дней!</b>",
                parse_mode="HTML", business_connection_id=streak["connection_id_a"],
                reply_markup=kb
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
                    chat_id=chat_id, title="Восстановление огонька",
                    description=f"Восстановить серию {streak['streak']} дней за 25 звезд",
                    payload=f"restore_{chat_id}", provider_token="", currency="XTR",
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

# ==================== CRON ====================

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    for s in get_all_streaks():
        if s["is_gray"] == 1: continue
        
        # Проверка на "ленивого" друга
        # Если один из участников натапал намного больше, чем другой
        taps_a = s["total_taps_a"] or 0
        taps_b = s["total_taps_b"] or 0
        if taps_a > 100 and taps_a > taps_b * 3:
            # Друг ленится
            try:
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text="⚠️ <b>Ваш друг один растит огонька!</b> Помогите ему, зайдите в Mini App и потапайте!",
                    parse_mode="HTML",
                    business_connection_id=s["connection_id_a"]
                )
            except: pass

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

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        peer_id = getattr(conn, 'user_chat_id', None)
        save_connection(conn.user.id, conn.id, peer_id)
        logger.info(f"✅ Подключение: {conn.user.id}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

# ==================== ЗАПУСК ====================

USER_STATES = {}

def main():
    if not TOKEN or not URL or not DATABASE_URL:
        logger.error("Не заданы переменные окружения")
        return
        
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("com", com_command))
    app.add_handler(CommandHandler("myfaier", myfaier_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("adm", adm_command))
    app.add_handler(CommandHandler("send", send_invite_command))
    app.add_handler(CommandHandler("l", force_check))
    
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_admin_phrase))
    
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
        listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)),
        url_path=TOKEN, webhook_url=f"{URL}/{TOKEN}", drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "business_connection", "business_message", "edited_business_message", "deleted_business_messages"]
    )

if __name__ == "__main__":
    main()
