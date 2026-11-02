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
BOT_USERNAME = os.environ.get("BOT_USERNAME", "fier_zyza_bot") # Укажи юзернейм бота без @
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Проверяем, есть ли Deep Link параметр (например: /start invite_12345678)
    if context.args and context.args[0].startswith("invite_"):
        initiator_id = int(context.args[0].split("_")[1])
        
        # Создаем серию
        chat_id = user.id
        conn_id = db.get_connection(initiator_id)
        db.create_streak(chat_id, conn_id, initiator_id, user.id)
        
        await update.message.reply_text(
            f"🔥 <b>Серия начата! День 1.</b>\n\n"
            f"Ты принял приглашение от друга.\n\n"
            "Каждый день в 00:00 по Екатеринбургу я буду присылать сюда кнопку. Нажимать должны оба, чтобы огонек горел.",
            parse_mode="HTML"
        )
        return

    # Обычный старт
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька.\n\n"
        "📌 <b>Инструкция:</b>\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Напиши /faier.\n"
        "3. Я дам тебе ссылку, которую нужно отправить другу.\n"
        "4. Друг нажимает на ссылку и запускает бота — серия начинается!\n\n"
        "⚠️ <b>Важно:</b> без Business-подключения я не смогу отправлять напоминания!",
        parse_mode="HTML"
    )

async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private":
        return
        
    conn_id = db.get_connection(user.id)
    if not conn_id:
        await update.message.reply_text(
            "❌ <b>Ошибка:</b> Бот не подключен в Business-режиме!\n"
            "Зайди в Настройки → Telegram Business → Чат-боты и добавь меня.",
            parse_mode="HTML"
        )
        return

    # Генерируем ссылку-приглашение
    invite_link = f"https://t.me/{BOT_USERNAME}?start=invite_{user.id}"
    
    text = (
        "👤 <b>Шаг 1:</b> Скопируй эту ссылку и отправь её другу в обычном чате:\n\n"
        f"<code>{invite_link}</code>\n\n"
        "Как только друг нажмет на неё и запустит бота, ваша серия начнется автоматически!\n"
        "⚠️ Убедись, что ты недавно общался с ним (в течение 24 часов), чтобы бот мог присылать уведомления от твоего имени."
    )
    
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

# ==================== КНОПКИ ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "keep_":
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
        
        if not streak:
            await query.answer("Серия не найдена.")
            return

        if streak["restore_count"] < 3:
            if db.restore_streak(chat_id, paid=False):
                await query.edit_message_text("🔥 <b>Огонек восстановлен!</b>", parse_mode="HTML")
        else:
            await query.message.reply_text("❌ Бесплатные попытки закончились. Используйте /restore.")

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
