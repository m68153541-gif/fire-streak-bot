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
PENDING_INVITES = {}

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька.\n\n"
        "📌 <b>Инструкция:</b>\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b> (я должен получить доступ к сообщениям).\n"
        "2. Напиши /faier и укажи @username друга.\n"
        "3. Друг получит сообщение от твоего имени с кнопкой «Принять».\n"
        "4. Каждый день в 00:00 ЕКБ я буду присылать кнопку «Поддержать огонек». Нажать должны оба.\n"
        "5. Восстановление: 3 раза бесплатно, потом 25 ⭐.\n\n"
        "⚠️ <b>Важно:</b> без Business-подключения бот не сможет отправлять сообщения от твоего имени.",
        parse_mode="HTML"
    )

async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private":
        return
        
    conn_id = db.get_connection(user.id)
    if not conn_id:
        await update.message.reply_text("❌ Ошибка: Бот не подключен в Business-режиме! Зайди в Настройки → Telegram Business.")
        return

    PENDING_INVITES[user.id] = True
    await update.message.reply_text("👤 Введи @username друга (он должен был недавно тебе писать, чтобы Telegram разрешил отправку!):")

async def handle_username_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if not PENDING_INVITES.get(user.id):
        return
    if not text.startswith("@"):
        await update.message.reply_text("❌ Введи корректный @username.")
        return

    target_username = text[1:]
    PENDING_INVITES.pop(user.id, None)
    conn_id = db.get_connection(user.id)

    # Кнопки принять/отклонить
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{user.id}")]
    ])
    
    try:
        # Отправляем через Business API от имени пользователя
        await context.bot.send_message(
            chat_id=f"@{target_username}",
            text="🔥 <b>Привет! Давай растить огонек дружбы?</b>\n\nНажми кнопку ниже, чтобы начать серию.",
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=conn_id
        )
        await update.message.reply_text("✅ Приглашение отправлено другу от твоего имени!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки. Вы точно общались с ним в последние 24 часа?\n\nДетали: {e}")

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    streak = db.get_streak(chat.id)
    
    if not streak or streak["is_gray"] == 0:
        await update.message.reply_text("❌ У вас нет серого огонька.")
        return

    if streak["restore_count"] < 3:
        # Бесплатно
        if db.restore_streak(chat.id, paid=False):
            await update.message.reply_text(
                f"🔥 <b>Огонек восстановлен!</b>\n\nОсталось бесплатных попыток: {2 - streak['restore_count']}",
                parse_mode="HTML"
            )
    else:
        # За звезды
        await context.bot.send_invoice(
            chat_id=chat.id,
            title="Восстановление огонька",
            description=f"Восстановить серию {streak['streak']} дней за 25 звезд",
            payload=f"restore_{chat.id}",
            provider_token="",  # Для Stars оставляем пустым
            currency="XTR",
            prices=[LabeledPrice(label="Восстановление", amount=25)]
        )

# ==================== CALLBACKS (КНОПКИ) ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("acc_"):
        initiator_id = int(data.split("_")[1])
        chat_id = query.message.chat.id
        conn_id = db.get_connection(initiator_id)
        
        db.create_streak(chat_id, conn_id, initiator_id, user.id)
        
        await query.edit_message_text(
            "🔥 <b>Серия начата! День 1.</b>\n\n"
            "Каждый день нажимайте кнопку под сообщением бота, чтобы продлить.",
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
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Поддержать огонек (1/2)", callback_data="keep_")]]),
                    parse_mode="HTML"
                )
            else:
                await query.answer("Ты уже нажал!")
        
        elif status == "success":
            new_streak = result
            await query.message.delete() # Удаляем сообщение с кнопками
            
            streak = db.get_streak(chat_id)
            text = f"🔥 <b>Огонек продлен! Серия: {new_streak} дней.</b>"
            if new_streak in MILESTONES:
                text = f"🎉 <b>ПОЗДРАВЛЯЕМ! Серия достигла {new_streak} дней!</b>\nВы супер! 💪"
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                business_connection_id=streak["connection_id_a"]
            )

# ==================== ПЛАТЕЖИ (STARS) ====================

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if payment.invoice_payload.startswith("restore_"):
        chat_id = int(payment.invoice_payload.split("_")[1])
        db.restore_streak(chat_id, paid=True)
        await update.message.reply_text("🔥 <b>Огонек восстановлен за 25 ⭐!</b>", parse_mode="HTML")

# ==================== CRON (00:00 ЕКБ) ====================

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running midnight check...")
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

# ==================== ЗАПУСК ====================

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_username_input))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(BusinessConnectionHandler(on_business_connect))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Планировщик
    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))

    # Webhook
    PORT = int(os.environ.get("PORT", 10000))
    URL = os.environ.get("RENDER_EXTERNAL_URL")
    app.run_webhook(
        listen="0.0.0.0", port=PORT, url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}", drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
