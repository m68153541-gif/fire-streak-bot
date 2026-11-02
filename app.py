import os
import sqlite3
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TELEGRAM_TOKEN", "9027429698:AAF9f7vipHibEzomSClmLXRjpabRz0nu8BE")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
DB_PATH = "streaks.db"

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS streaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_a INTEGER,
        user_b INTEGER,
        chat_id INTEGER,
        streak INTEGER DEFAULT 0,
        last_active DATE,
        is_gray INTEGER DEFAULT 0,
        restore_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_a, user_b)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER,
        to_user INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_streak(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM streaks WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_streak(chat_id, streak, last_active, is_gray, restore_count=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if restore_count is not None:
        c.execute("UPDATE streaks SET streak=?, last_active=?, is_gray=?, restore_count=? WHERE chat_id=?",
                  (streak, last_active, is_gray, restore_count, chat_id))
    else:
        c.execute("UPDATE streaks SET streak=?, last_active=?, is_gray=? WHERE chat_id=?",
                  (streak, last_active, is_gray, chat_id))
    conn.commit()
    conn.close()

# --- ЛОГИКА ОГОНЬКА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🔥 Привет, {user.first_name}!\n\n"
        "Я — бот для поддержания серии огонька в личных переписках.\n\n"
        "📌 **Как это работает:**\n"
        "1. Ты добавляешь меня в Business-режим (настройки Telegram → Business → Чат-боты).\n"
        "2. Пишешь `/faier` и указываешь @username друга.\n"
        "3. Ему приходит приглашение. После принятия начинается серия.\n"
        "4. Каждый день до 00:00 по Екатеринбургу **оба** должны написать хоть одно сообщение.\n"
        "5. Если кто-то не написал — огонёк становится серым ⚪ и я напоминаю каждые 2 часа.\n"
        "6. Серия сгорает, если не продлить в течение суток. Восстановление: 3 раза бесплатно, потом 25 ⭐.\n\n"
        "⚠️ **Важно:** Чтобы я видел сообщения в твоих личных чатах, подключи меня в Business-режиме.\n"
        "Инструкция: Настройки → Telegram Business → Чат-боты → добавь меня."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("❌ Команда работает только в личных сообщениях.")
        return
    # Проверяем, есть ли уже серия
    row = get_streak(chat.id)
    if row:
        await update.message.reply_text(f"🔥 У вас уже есть серия с этим собеседником: {row[4]} дней.")
        return
    await update.message.reply_text("👤 Введи @username друга, с которым хочешь растить огонёк:")

async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.startswith("@") or len(text) < 2:
        await update.message.reply_text("❌ Введи корректный @username.")
        return
    # Здесь нужна логика поиска user_id по username (упрощённо — просим переслать сообщение)
    # В реальности лучше использовать forward или просить друга нажать /start
    await update.message.reply_text(
        f"✅ Приглашение для {text} создано (заглушка).\n"
        "В реальной версии здесь будет отправка сообщения другу с кнопками «Принять/Отклонить».\n"
        "Пока что попроси друга написать мне /start и перешли его сообщение сюда."
    )

# --- ОБРАБОТКА BUSINESS-СООБЩЕНИЙ ---
async def business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg:
        return
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    row = get_streak(chat_id)
    if row:
        # Обновляем активность
        today = datetime.now(TZ_EKB).date()
        last_active = row[5]
        if last_active != today:
            # Если день сменился, но оба написали — продлеваем
            # В упрощённом виде: просто обновляем last_active
            update_streak(chat_id, row[4], today.isoformat(), 0)
    # Дополнительная логика подсчёта «оба написали» требует хранения флагов за день

async def handle_invite_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("accept_"):
        # Логика принятия приглашения
        await query.edit_message_text("✅ Серия начата! День 1. Продолжайте общаться каждый день 🔥")
    elif data.startswith("decline_"):
        await query.edit_message_text("❌ Приглашение отклонено.")

# --- ФОНОВЫЕ ЗАДАЧИ ---
async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    """Проверка в 00:00 ЕКБ: если кто-то не писал — серый огонь."""
    now = datetime.now(TZ_EKB)
    today = now.date()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM streaks WHERE is_gray = 0")
    rows = c.fetchall()
    for row in rows:
        chat_id = row[3]
        last_active = row[5]
        # Если last_active не сегодня — значит не продлили
        if last_active != today.isoformat():
            update_streak(chat_id, row[4], last_active, 1)
            # Отправляем сообщение в чат (через business_connection_id, если есть)
            # В упрощённом виде — шлём обычное сообщение от бота
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚪ Огонёк стал серым. Пора продлить серию! Напишите оба хотя бы по одному сообщению до 00:00 по Екатеринбургу."
                )
            except:
                pass
    conn.close()

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание каждые 2 часа для серых огоньков."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM streaks WHERE is_gray = 1")
    rows = c.fetchall()
    for row in rows:
        chat_id = row[3]
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚪ Напоминание: огонёк серый. Продлите серию — напишите оба! 🔥"
            )
        except:
            pass
    conn.close()

async def celebrate_milestones(context: ContextTypes.DEFAULT_TYPE):
    """Поздравления на рубежах: 10, 30, 60, 100 и т.д."""
    milestones = [10, 30, 60, 100, 200, 300, 500, 700, 1000, 2000, 5000]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM streaks WHERE streak IN ({})".format(",".join("?"*len(milestones))), milestones)
    rows = c.fetchall()
    for row in rows:
        chat_id = row[3]
        streak = row[4]
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉🔥 ПОЗДРАВЛЯЕМ! Ваша серия достигла {streak} дней! Вы очень дружные и активные! Так держать! 💪✨"
            )
        except:
            pass
    conn.close()

# --- ЗАПУСК ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, business_message))
    app.add_handler(CallbackQueryHandler(handle_invite_response))

    # Планировщик
    job_queue = app.job_queue
    job_queue.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    job_queue.run_repeating(remind_gray, interval=timedelta(hours=2), first=10)
    job_queue.run_repeating(celebrate_milestones, interval=timedelta(hours=1), first=60)

    app.run_polling()

if __name__ == "__main__":
    main()
