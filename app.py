import os
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, PreCheckoutQueryHandler, BusinessConnectionHandler
)

import database as db

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

# Словарь для хранения состояний: user_id -> {"state": "...", "target_id": ...}
USER_STATES = {}

# Временное хранилище для chat_id, полученных из business_message
# Ключ: (user_id_инициатора, user_id_друга) -> chat_id
PEER_CACHE = {}

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька.\n\n"
        "📌 <b>Инструкция:</b>\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Попроси друга написать тебе любое сообщение в ЛС (это нужно, чтобы я увидел чат).\n"
        "3. Напиши /faier и введи числовой ID друга (узнать через @userinfobot).\n"
        "4. Я отправлю приглашение прямо в ваше ЛС от твоего имени.\n\n"
        "⚠️ <b>Важно:</b> без Business-подключения я не смогу писать от твоего имени!",
        parse_mode="HTML"
    )

async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private":
        return
        
    conn_id = db.get_connection(user.id)
    if not conn_id:
        await update.message.reply_text("❌ Ошибка: Бот не подключен в Business-режиме!")
        return

    USER_STATES[user.id] = {"state": "waiting_id"}
    await update.message.reply_text(
        "👤 <b>Шаг 1:</b> Введи числовой ID друга (например, 123456789).\n\n"
        "Если не знаешь, попроси его написать боту @userinfobot.\n"
        "⚠️ Убедись, что друг недавно писал тебе в ЛС!",
        parse_mode="HTML"
    )

async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state_data = USER_STATES.get(user.id)
    
    if not state_data or state_data.get("state") != "waiting_id":
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Введи числовой ID (только цифры).")
        return

    target_id = int(text)
    USER_STATES.pop(user.id, None)
    conn_id = db.get_connection(user.id)

    # Ищем chat_id в кэше (если друг писал)
    chat_id = PEER_CACHE.get((user.id, target_id))
    
    # Если в кэше нет, пробуем использовать target_id как chat_id (для ЛС это часто работает)
    if not chat_id:
        chat_id = target_id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}_{target_id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{user.id}")]
    ])
    
    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🔥 <b>Привет! {user.first_name} предлагает растить огонек дружбы.</b>\n\nНажми кнопку ниже:",
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=conn_id
        )
        await update.message.reply_text("✅ Приглашение успешно отправлено в ваше ЛС!")
    except Exception as e:
        error_str = str(e)
        if "BUSINESS_PEER_INVALID" in error_str:
            await update.message.reply_text(
                "❌ <b>Ошибка:</b> Telegram не разрешает отправить сообщение.\n\n"
                "Попроси друга написать тебе в ЛС любое сообщение, а затем повтори /faier.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка отправки: {e}")

# ==================== ОБРАБОТКА BUSINESS СООБЩЕНИЙ ====================

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловим сообщения из бизнес-подключения, чтобы запомнить chat_id"""
    msg = update.business_message
    if not msg:
        return

    # Кто владелец бизнес-подключения? (тот, кто подключил бота)
    # В Telegram API update.business_message содержит chat.id и from_user.id.
    # Нам нужно понять, кто из них "владелец", а кто "друг".
    
    # В бизнес-сообщениях from_user - это тот, кто написал.
    # chat.id - это ID чата (для ЛС это ID собеседника).
    
    # Сохраняем связку: (владелец, собеседник) -> chat_id
    # Владельца мы можем узнать по business_connection_id, но проще сохранить оба варианта.
    chat_id = msg.chat.id
    from_user_id = msg.from_user.id
    
    # Сохраняем в кэш оба направления
    PEER_CACHE[(from_user_id, chat_id)] = chat_id
    PEER_CACHE[(chat_id, from_user_id)] = chat_id
    
    logger.info(f"Business message cached: from={from_user_id}, chat={chat_id}")

# ==================== КНОПКИ ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id = int(parts[1])
        receiver_id = int(parts[2])
        chat_id = receiver_id 
        conn_id = db.get_connection(initiator_id)
        
        db.create_streak(chat_id, conn_id, initiator_id, receiver_id)
        await query.edit_message_text(
            "🔥 <b>Серия начата! День 1.</b>\n\nКаждый день в 00:00 ЕКБ я буду присылать сюда кнопку. Нажимать должны оба.",
            parse_mode="HTML"
        )
        
    elif data.startswith("dec_"):
        await query.edit_message_text("❌ Приглашение отклонено.")

    elif data == "keep_":
        chat_id = query.message.chat.id
        status, result = db.press_button(chat_id, user.id)
        
        if status == "waiting":
            if result == 1:
                await query.answer("✅ Ты поддержал огонек! Ждем второго.")
                await query.edit_message_text(
                    f"⚪ <b>Огонек ждет подтверждения...</b>\n\nНажал(а): {user.first_name}\nСтатус: 1/2",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Поддержать (1/2)", callback_data="keep_")]]),
                    parse_mode="HTML"
                )
            else:
                await query.answer("Ты уже нажал!")
        
        elif status == "success":
            new_streak = result
            await query.message.delete()
            streak = db.get_streak(chat_id)
            text = f"🔥 <b>Огонек продлен! Серия: {new_streak} дней.</b>"
            if new_streak in MILESTONES:
                text = f"🎉 <b>ПОЗДРАВЛЯЕМ! Серия достигла {new_streak} дней!</b> Вы супер! 💪"
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML", business_connection_id=streak["connection_id_a"]
            )

    elif data.startswith("restore_"):
        chat_id = int(data.split("_")[1])
        streak = db.get_streak(chat_id)
        if not streak: return
        if streak["restore_count"] < 3:
            if db.restore_streak(chat_id, paid=False):
                await query.edit_message_text("🔥 <b>Огонек восстановлен!</b>", parse_mode="HTML")

# ==================== ПЛАТЕЖИ И CRON ====================

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if payment.invoice_payload.startswith("restore_"):
        chat_id = int(payment.invoice_payload.split("_")[1])
        db.restore_streak(chat_id, paid=True)
        await update.message.reply_text("🔥 <b>Огонек восстановлен за 25 ⭐!</b>", parse_mode="HTML")

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    streaks = db.get_all_streaks()
    for s in streaks:
        if s["is_gray"] == 1:
            continue
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать огонек", callback_data="keep_")]])
        try:
            msg = await context.bot.send_message(
                chat_id=s["chat_id"],
                text="⚪ <b>Огонек стал серым.</b>\n\nНажмите кнопку ниже, чтобы продлить серию (нужно обоим):",
                parse_mode="HTML", reply_markup=keyboard, business_connection_id=s["connection_id_a"]
            )
            db.set_gray(s["chat_id"], msg.message_id)
        except Exception as e:
            logger.error(f"Failed midnight: {e}")

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    streaks = db.get_unsent_reminders()
    for s in streaks:
        if not s["gray_since"]:
            continue
        gray_since = datetime.fromisoformat(s["gray_since"])
        if datetime.now(TZ_EKB) - gray_since > timedelta(days=7):
            db.kill_streak(s["chat_id"])
            try:
                await context.bot.send_message(
                    chat_id=s["chat_id"], text="💀 <b>Серия окончательно сгорела.</b>", 
                    parse_mode="HTML", business_connection_id=s["connection_id_a"]
                )
            except: pass
            continue
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")],
            [InlineKeyboardButton("♻️ Восстановить", callback_data=f"restore_{s['chat_id']}")]
        ])
        try:
            await context.bot.send_message(
                chat_id=s["chat_id"], text="⚪ <b>Напоминание:</b> огонек серый. Пора его продлить!",
                parse_mode="HTML", reply_markup=keyboard, business_connection_id=s["connection_id_a"]
            )
        except Exception as e:
            logger.error(f"Failed remind: {e}")

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN is missing!")
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_message_input))
    
    # Ловим бизнес-сообщения
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))
    
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(BusinessConnectionHandler(on_business_connect))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    PORT = int(os.environ.get("PORT", 10000))
    URL = os.environ.get("RENDER_EXTERNAL_URL")
    
    app.run_webhook(
        listen="0.0.0.0", port=PORT, url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}", drop_pending_updates=True, bootstrap_retries=5
    )

if __name__ == "__main__":
    main()
