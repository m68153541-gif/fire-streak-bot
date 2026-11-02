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
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000, 2000, 5000]
PENDING_INVITES = {}


# ========== КОМАНДЫ (логика без изменений) ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька в личных переписках.\n\n"
        "📌 <b>Как это работает:</b>\n"
        "1. Подключи меня в <b>Telegram Business → Чат-боты</b>.\n"
        "2. Напиши /faier и укажи @username друга.\n"
        "3. Друг получит приглашение. После принятия начнётся серия.\n"
        "4. Каждый день <b>оба</b> должны написать хотя бы одно сообщение до 00:00 ЕКБ.\n"
        "5. Если кто-то не написал — огонёк станет серым ⚪.\n"
        "6. Восстановление: 3 раза бесплатно, потом 25 ⭐.\n\n"
        "⚠️ <b>Важно:</b> подключи меня в Business-режиме, иначе я не увижу сообщения."
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("❌ Команда работает только в личных сообщениях.")
        return
    existing = db.get_streak_by_chat(chat.id)
    if existing:
        await update.message.reply_text(
            f"🔥 У вас уже есть серия: <b>{existing['streak']} дней</b>.",
            parse_mode="HTML"
        )
        return
    PENDING_INVITES[user.id] = True
    await update.message.reply_text("👤 Введи @username друга:")


async def handle_username_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    if not PENDING_INVITES.get(user.id):
        return
    if not text.startswith("@") or len(text) < 2:
        await update.message.reply_text("❌ Введи корректный @username.")
        return
    target_username = text[1:]
    PENDING_INVITES.pop(user.id, None)
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO pending_invites (from_user_id, from_username, to_user_id) VALUES (?, ?, ?)",
        (user.id, user.username or user.first_name, 0)
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ Приглашение для @{target_username} создано.\n\n"
        "Попроси друга написать мне /start и перешли его сообщение сюда.",
        parse_mode="HTML"
    )


async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)
        logger.info(f"Business connection saved: user={conn.user.id}")
    else:
        logger.info(f"Business connection disabled: user={conn.user.id}")


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg:
        return
    chat_id = msg.chat.id
    from_user = msg.from_user.id
    connection_id = msg.business_connection_id
    streak = db.get_streak_by_chat(chat_id)
    if not streak:
        return
    db.mark_user_wrote(chat_id, from_user)
    new_streak = db.try_advance_streak(chat_id)
    if new_streak and new_streak in MILESTONES:
        text = (
            f"🎉🔥 <b>ПОЗДРАВЛЯЕМ!</b>\n\n"
            f"Ваша серия достигла <b>{new_streak} дней</b>!\n"
            f"Вы очень дружные! 💪✨"
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML",
                business_connection_id=connection_id
            )
        except Exception as e:
            logger.error(f"Failed to send milestone: {e}")


async def handle_invite_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("accept_"):
        await query.edit_message_text(
            "✅ <b>Серия начата! День 1.</b>\n\nПродолжайте общаться каждый день 🔥",
            parse_mode="HTML"
        )
    elif data.startswith("decline_"):
        await query.edit_message_text("❌ Приглашение отклонено.")


async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running midnight check...")
    streaks = db.get_all_streaks()
    for s in streaks:
        if s["is_gray"] == 1:
            continue
        today = datetime.now(TZ_EKB).date().isoformat()
        if s["last_active_date"] != today:
            db.set_gray(s["chat_id"])
            try:
                if s["business_connection_id"]:
                    await context.bot.send_message(
                        chat_id=s["chat_id"],
                        text="⚪ <b>Огонёк стал серым.</b>\n\nПора продлить серию!",
                        parse_mode="HTML",
                        business_connection_id=s["business_connection_id"]
                    )
            except Exception as e:
                logger.error(f"Failed to send gray notice: {e}")


async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running gray reminder...")
    streaks = db.get_gray_streaks()
    for s in streaks:
        gray_since = s["gray_since"]
        if not gray_since:
            continue
        gray_dt = datetime.fromisoformat(gray_since)
        now = datetime.now(TZ_EKB)
        if now - gray_dt > timedelta(days=7):
            conn = db.get_conn()
            conn.execute("DELETE FROM streaks WHERE chat_id = ?", (s["chat_id"],))
            conn.commit()
            conn.close()
            try:
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text="💀 <b>Серия окончательно сгорела.</b>",
                    parse_mode="HTML",
                    business_connection_id=s["business_connection_id"]
                )
            except:
                pass
            continue
        try:
            await context.bot.send_message(
                chat_id=s["chat_id"],
                text="⚪ <b>Напоминание:</b> огонёк серый. Продлите серию! 🔥",
                parse_mode="HTML",
                business_connection_id=s["business_connection_id"]
            )
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        return
    streak = db.get_streak_by_chat(chat.id)
    if not streak or streak["is_gray"] == 0:
        await update.message.reply_text("❌ Нет серого огонька.")
        return
    if streak["restore_count"] < 3:
        success = db.restore_streak(chat.id, paid=False)
        if success:
            await update.message.reply_text(
                f"🔥 <b>Огонёк восстановлен!</b>\n\nОсталось бесплатных: {2 - streak['restore_count']}",
                parse_mode="HTML"
            )
    else:
        await context.bot.send_invoice(
            chat_id=chat.id,
            title="Восстановление огонька",
            description=f"Серия {streak['streak']} дней за 25 звёзд",
            payload=f"restore_{chat.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Восстановление", amount=25)]
        )


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if payment.invoice_payload.startswith("restore_"):
        chat_id = int(payment.invoice_payload.split("_")[1])
        db.restore_streak(chat_id, paid=True)
        await update.message.reply_text(
            "🔥 <b>Огонёк восстановлен за 25 ⭐!</b>",
            parse_mode="HTML"
        )


# ========== ЗАПУСК (Webhook вместо Polling) ==========

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    # Все обработчики (без изменений)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_username_input))
    app.add_handler(BusinessConnectionHandler(handle_business_connection))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))
    app.add_handler(CallbackQueryHandler(handle_invite_response))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Планировщик
    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    # Webhook-запуск для Render
    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
    if not RENDER_EXTERNAL_URL:
        raise ValueError("RENDER_EXTERNAL_URL environment variable is not set")

    logger.info(f"Starting webhook on port {PORT}, url: {RENDER_EXTERNAL_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,          # секретный путь, чтобы никто не дёргал webhook
        webhook_url=f"{RENDER_EXTERNAL_URL}/{TOKEN}",
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
