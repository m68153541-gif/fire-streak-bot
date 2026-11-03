import os
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, BusinessConnectionHandler, PreCheckoutQueryHandler
)

import database as db

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
URL = os.environ.get("RENDER_EXTERNAL_URL") # Render сам подставит сюда твой URL
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")

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
    conn_id = db.get_connection(user.id)
    
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
    msg = update.business_message
    if not msg: return

    chat_id = msg.chat.id
    conn_id = msg.business_connection_id
    
    conn = db.get_conn()
    row = conn.execute("SELECT user_id FROM user_connections WHERE connection_id = ?", (conn_id,)).fetchone()
    conn.close()
    if not row: return
        
    owner_id = row["user_id"]

    existing = db.get_streak(chat_id)
    if existing:
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=f"🔥 Серия уже есть: <b>{existing['streak']} дней</b>.",
                parse_mode="HTML", business_connection_id=conn_id
            )
        except: pass
        return

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
        logger.error(f"Ошибка: {e}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id, receiver_id = int(parts[1]), int(parts[2])
        chat_id = query.message.chat.id 
        conn_id = db.get_connection(initiator_id)
        db.create_streak(chat_id, conn_id, initiator_id, receiver_id)
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
        status, result = db.press_button(chat_id, user.id)
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
            streak = db.get_streak(chat_id)
            await context.bot.send_message(
                chat_id=chat_id, text=f"🔥 <b>Серия: {new_streak} дней!</b>",
                parse_mode="HTML", business_connection_id=streak["connection_id_a"]
            )

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)
        logger.info(f"✅ Подключение: {conn.user.id}")
    else:
        db.delete_connection(conn.user.id)
        logger.info(f"❌ Отключение: {conn.user.id}")

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    for s in db.get_all_streaks():
        if s["is_gray"] == 1: continue
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать огонек", callback_data="keep_")]])
        try:
            msg = await context.bot.send_message(
                chat_id=s["chat_id"], text="⚪ <b>Огонек стал серым.</b>\n\nНажмите кнопку (нужно обоим):",
                parse_mode="HTML", reply_markup=keyboard, business_connection_id=s["connection_id_a"]
            )
            db.set_gray(s["chat_id"], msg.message_id)
        except Exception as e:
            logger.error(f"Failed midnight: {e}")

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    for s in db.get_unsent_reminders():
        if not s["gray_since"]: continue
        if datetime.now(TZ_EKB) - datetime.fromisoformat(s["gray_since"]) > timedelta(days=7):
            db.kill_streak(s["chat_id"])
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

# ==================== ЗАПУСК (WEBHOOK) ====================

def main():
    if not TOKEN: raise ValueError("No token")
    if not URL: raise ValueError("No RENDER_EXTERNAL_URL")
    
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    # Регистрация всех хендлеров
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("l", check_connection))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE & filters.COMMAND, faier_in_chat))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(BusinessConnectionHandler(on_business_connect))
    app.add_error_handler(error_handler)

    # Задачи по расписанию
    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    # Запуск через Webhook (Render это любит)
    logger.info(f"Starting webhook on {URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
