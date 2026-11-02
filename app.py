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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

USER_STATES = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька.\n\n"
        "📌 <b>Инструкция:</b>\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Напиши /faier и следуй инструкциям.\n"
        "3. Друг получит приглашение с кнопкой «Принять».\n"
        "4. В 00:00 ЕКБ я пришлю кнопку «Поддержать огонек».",
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

    USER_STATES[user.id] = {"state": "waiting_reply"}
    await update.message.reply_text(
        "👤 <b>Шаг 1:</b> Сделай свайп влево по <b>любому сообщению друга</b> в вашем чате и ответь на него (напиши что угодно, например «Привет»).\n\n"
        "Затем перешли это сообщение сюда.",
        parse_mode="HTML"
    )

# Обработка сообщений (ждем ответ на сообщение друга)
async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state_data = USER_STATES.get(user.id)
    
    if not state_data:
        return

    if state_data.get("state") == "waiting_reply":
        msg = update.message
        # Проверяем, есть ли reply_to_message (на кого ответили)
        if not msg.reply_to_message:
            await msg.reply_text("❌ Ты не ответил на сообщение друга. Сделай свайп по его сообщению и ответь.")
            return
            
        # Получаем данные того, на кого ответили
        replied_user = msg.reply_to_message.from_user
        
        # Если ответили сами себе или боту - ошибка
        if replied_user.id == user.id or replied_user.is_bot:
            await msg.reply_text("❌ Это твое сообщение или бот. Ответь на сообщение друга.")
            return

        target_id = replied_user.id
        target_name = replied_user.first_name
        
        USER_STATES[user.id] = {"state": "waiting_username", "target_id": target_id, "target_name": target_name}
        
        await msg.reply_text(
            f"✅ ID друга ({target_name}) получен!\n\n"
            f"<b>Шаг 2:</b> Введи его @username (например: @ivan_petrov).",
            parse_mode="HTML"
        )

    elif state_data.get("state") == "waiting_username":
        text = update.message.text.strip()
        if not text.startswith("@"):
            await update.message.reply_text("❌ Введи корректный @username (начинается с @).")
            return

        target_id = state_data["target_id"]
        USER_STATES.pop(user.id, None) # Сбрасываем

        conn_id = db.get_connection(user.id)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}_{target_id}"),
             InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{user.id}")]
        ])
        
        try:
            await context.bot.send_message(
                chat_id=target_id, 
                text=f"🔥 <b>Привет! {user.first_name} предлагает растить огонек дружбы.</b>\n\nНажми кнопку ниже:",
                parse_mode="HTML",
                reply_markup=keyboard,
                business_connection_id=conn_id
            )
            await update.message.reply_text("✅ Приглашение успешно отправлено другу!")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка отправки: {e}")

# ==================== КНОПКИ И ОСТАЛЬНАЯ ЛОГИКА ====================

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
            "🔥 <b>Серия начата! День 1.</b>\n\n"
            "Каждый день в 00:00 я буду присылать сюда кнопку. Нажимать должны оба.",
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
                chat_id=chat_id, text=text, parse_mode="HTML",
                business_connection_id=streak["connection_id_a"]
            )

    elif data.startswith("restore_"):
        chat_id = int(data.split("_")[1])
        if db.restore_streak(chat_id, paid=False):
            await query.edit_message_text("🔥 <b>Огонек восстановлен!</b>", parse_mode="HTML")

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
                parse_mode="HTML",
                reply_markup=keyboard,
                business_connection_id=s["connection_id_a"]
            )
            db.set_gray(s["chat_id"], msg.message_id)
        except Exception as e:
            logger.error(f"Failed midnight: {e}")

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    streaks = db.get_unsent_reminders()
    for s in streaks:
        gray_since = datetime.fromisoformat(s["gray_since"])
        if datetime.now(TZ_EKB) - gray_since > timedelta(days=7):
            db.kill_streak(s["chat_id"])
            try:
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text="💀 <b>Серия окончательно сгорела.</b>\n\nНачните заново с /faier.",
                    parse_mode="HTML",
                    business_connection_id=s["connection_id_a"]
                )
            except: pass
            continue

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")],
            [InlineKeyboardButton("♻️ Восстановить", callback_data=f"restore_{s['chat_id']}")]
        ])
        
        try:
            await context.bot.send_message(
                chat_id=s["chat_id"],
                text="⚪ <b>Напоминание:</b> огонек серый. Пора его продлить!",
                parse_mode="HTML",
                reply_markup=keyboard,
                business_connection_id=s["connection_id_a"]
            )
        except Exception as e:
            logger.error(f"Failed remind: {e}")

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    
    # Теперь обрабатываем ВСЕ текстовые сообщения (и ответы) через одну функцию
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_message_input))
    
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
        webhook_url=f"{URL}/{TOKEN}", drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
