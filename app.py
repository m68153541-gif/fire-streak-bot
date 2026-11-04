import os
import logging
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import random
import uvicorn

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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

USER_STATES = {}

# Создаем FastAPI приложение
api_app = FastAPI()

# Создаем бота
application = Application.builder().token(TOKEN).build()

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
                peer_chat_id BIGINT
            )
        """)
        conn.commit()
        conn.close()
        logger.info("✅ БД инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")

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

def get_user_streaks(user_id):
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT * FROM streaks WHERE user_a = %s OR user_b = %s ORDER BY streak DESC", (user_id, user_id))
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

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

async def get_username(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        if chat.username:
            return f"@{chat.username}"
        return chat.first_name
    except:
        return f"ID:{user_id}"

# ==================== ХЕНДЛЕРЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args and context.args[0].startswith("bizChat"):
        try:
            chat_id = int(context.args[0][7:])
            save_connection(user.id, get_connection(user.id), chat_id)
            await update.message.reply_text(f"✅ Я запомнил этот чат (ID: {chat_id}).\n\nТеперь напиши /send.")
        except: pass
        return

    text = (
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "<b>Инструкция по запуску огонька:</b>\n\n"
        "1️⃣ <b>Подключи меня:</b> Настройки → Telegram Business → Чат-боты → добавь @fier_zyzabot. Включи ВСЕ права (Читать, Отвечать).\n\n"
        "2️⃣ <b>Вызови меня в чате:</b> В чате с другом нажми на 3 полоски вверху → Управление → выбери меня.\n\n"
        "3️⃣ <b>Вернись сюда и напиши /send.</b>\n\n"
        "4️⃣ <b>Когда друг примет приглашение</b>, начнется серия. В 00:00 по ЕКБ жди кнопку «Поддержать».\n\n"
        "⚠️ Если друг недавно не писал, Telegram может выдать ошибку. Попроси его написать тебе."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Перейти к подключению", url="tg://settings/edit")],
        [InlineKeyboardButton("📖 Все команды", callback_data="help")]
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def com_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📋 <b>Команды:</b>\n\n/start - Инструкция\n/send - Пригласить друга\n/myfaier - Мои огоньки\n/l - Проверка\n/adm - Рассылка (админ)"
    await update.message.reply_text(text, parse_mode="HTML")

async def myfaier_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    streaks = get_user_streaks(user.id)
    
    if not streaks:
        await update.message.reply_text("❌ У вас пока нет активных огоньков.")
        return
        
    text = "🔥 <b>Ваши огоньки:</b>\n\n"
    for s in streaks:
        partner_id = s["user_b"] if s["user_a"] == user.id else s["user_a"]
        partner_name = await get_username(context, partner_id)
        text += f"{partner_name} — <b>Серия: {s['streak']} дней</b>\n"
            
    await update.message.reply_text(text, parse_mode="HTML")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет прав.")
        return
        
    streaks = get_all_streaks()
    text = "👑 <b>Все огоньки:</b>\n\n"
    for s in streaks:
        name_a = await get_username(context, s["user_a"])
        name_b = await get_username(context, s["user_b"])
        text += f"{name_a} ↔️ {name_b} (Серия: {s['streak']}, Тапы: {s['total_taps_a']}/{s['total_taps_b']})\n"
    await update.message.reply_text(text[:4000], parse_mode="HTML")

async def adm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет прав.")
    USER_STATES[update.effective_user.id] = {"state": "waiting_admin_phrase"}
    await update.message.reply_text("👑 Введите фразу для рассылки:")

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
                chat_id=s["chat_id"], text=phrase, business_connection_id=s["connection_id_a"]
            )
            count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки в {s['chat_id']}: {e}")
            
    await update.message.reply_text(f"✅ Отправлено в {count} чатов.")

async def send_invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = get_connection(user.id)
    peer_chat_id = get_peer_chat_id(user.id)
    
    if not conn_id: return await update.message.reply_text("❌ Бот не подключен.")
    if not peer_chat_id: return await update.message.reply_text("❌ Сначала нажми кнопку бота в чате с другом.")
    
    if get_streak(peer_chat_id): return await update.message.reply_text("🔥 Серия уже есть.")
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}_{peer_chat_id}")]])
    try:
        await context.bot.send_message(
            chat_id=peer_chat_id, text="🔥 <b>Предложение растить огонек!</b>",
            parse_mode="HTML", reply_markup=keyboard, business_connection_id=conn_id
        )
        await update.message.reply_text("✅ Отправлено!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = get_connection(user.id)
    if not conn_id: return await update.message.reply_text("❌ Нет подключения.")
    try:
        conn_info = await context.bot.get_business_connection(conn_id)
        text = f"ID: {conn_info.id}\ncan_reply: {conn_info.rights.can_reply if conn_info.rights else 'Нет'}\npeer_chat_id: {get_peer_chat_id(user.id)}"
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg: return
    
    conn = get_conn()
    c = conn.cursor(cursor_factory=DictCursor)
    c.execute("SELECT user_id FROM user_connections WHERE connection_id = %s", (msg.business_connection_id,))
    row = c.fetchone()
    conn.close()
    if not row: return
    
    if msg.from_user.id == row["user_id"]: return
    streak = get_streak(msg.chat.id)
    if not streak: return

    count = increment_message_count(msg.chat.id)
    if count % 20 == 0:
        phrases = ["Вы так мило общаетесь! 💬", "Я всё вижу! 👀❤️", "Продолжайте! ✨"]
        phrase = random.choice(phrases)
        if streak["user_a"] == ADMIN_ID or streak["user_b"] == ADMIN_ID:
            phrase = "Вы так мило общаетесь! Я аж завидую! 💬🔥"
        try:
            await context.bot.send_message(chat_id=msg.chat.id, text=phrase, business_connection_id=msg.business_connection_id)
        except: pass

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "help":
        await query.message.edit_text("📋 Команды: /start, /send, /myfaier, /l, /adm", parse_mode="HTML")
        return
        
    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id, receiver_id = int(parts[1]), int(parts[2])
        chat_id = query.message.chat.id 
        conn_id = get_connection(initiator_id)
        create_streak(chat_id, conn_id, initiator_id, receiver_id)
        await query.edit_message_text("🔥 <b>Серия начата!</b>", parse_mode="HTML")
        try:
            await context.bot.send_message(chat_id=chat_id, text="🔥 <b>Огонек зажжен!</b>", business_connection_id=conn_id)
        except: pass

    elif data == "keep_":
        chat_id = query.message.chat.id
        status, result = press_button(chat_id, user.id)
        if status == "waiting" and result == 1:
            await query.edit_message_text(f"⚪ Ждем второго... 1/2", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Поддержать", callback_data="keep_")]]))
        elif status == "success":
            new_streak = result
            await query.message.delete()
            streak = get_streak(chat_id)
            await context.bot.send_message(
                chat_id=chat_id, text=f"🔥 <b>Серия: {new_streak} дней!</b>",
                parse_mode="HTML", business_connection_id=streak["connection_id_a"]
            )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if payment.invoice_payload.startswith("restore_"):
        chat_id = int(payment.invoice_payload.split("_")[1])
        restore_streak(chat_id, paid=True)
        await update.message.reply_text("🔥 <b>Восстановлено за 25 ⭐!</b>", parse_mode="HTML")

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    for s in get_all_streaks():
        if s["is_gray"] == 1: continue
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")]])
        try:
            msg = await context.bot.send_message(
                chat_id=s["chat_id"], text="⚪ <b>Огонек стал серым.</b>\n\nНажмите кнопку (нужно обоим):",
                reply_markup=keyboard, business_connection_id=s["connection_id_a"]
            )
            set_gray(s["chat_id"], msg.message_id)
        except Exception as e: logger.error(f"Failed midnight: {e}")

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    for s in get_unsent_reminders():
        if not s["gray_since"]: continue
        if datetime.now(TZ_EKB) - datetime.fromisoformat(s["gray_since"]) > timedelta(days=7):
            kill_streak(s["chat_id"])
            continue
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")]])
        try:
            await context.bot.send_message(chat_id=s["chat_id"], text="⚪ Напоминание: огонек серый!", reply_markup=keyboard, business_connection_id=s["connection_id_a"])
        except: pass

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        save_connection(conn.user.id, conn.id)
        logger.info(f"✅ Подключение: {conn.user.id}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

# ==================== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ====================

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("com", com_command))
application.add_handler(CommandHandler("myfaier", myfaier_command))
application.add_handler(CommandHandler("admin", admin_command))
application.add_handler(CommandHandler("adm", adm_command))
application.add_handler(CommandHandler("send", send_invite_command))
application.add_handler(CommandHandler("l", force_check))

application.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))
application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_admin_phrase))

application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(BusinessConnectionHandler(on_business_connect))
application.add_handler(PreCheckoutQueryHandler(pre_checkout))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
application.add_error_handler(error_handler)

# ==================== WEBHOOK ====================

@api_app.post(f"/{TOKEN}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return Response(status_code=200)

# ==================== СТАТИКА ====================

api_app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")

@api_app.get("/")
async def root():
    return FileResponse("webapp/index.html")

# ==================== ЗАПУСК ====================

@api_app.on_event("startup")
async def on_startup():
    init_db()
    await application.initialize()
    await application.bot.set_webhook(f"{URL}/{TOKEN}")
    await application.start()
    logger.info("✅ Бот запущен и вебхук установлен")

@api_app.on_event("shutdown")
async def on_shutdown():
    await application.stop()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(api_app, host="0.0.0.0", port=port)
