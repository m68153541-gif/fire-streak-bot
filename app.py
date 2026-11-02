import os
import asyncio
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

TOKEN = os.environ.get("TELEGRAM_TOKEN", "9027429698:AAF9f7vipHibEzomSClmLXRjpabRz0nu8BE")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")

MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000, 2000, 5000]
PENDING_INVITES = {}  # user_id -> target_username


# ========== КОМАНДЫ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька в личных переписках.\n\n"
        "📌 <b>Как это работает:</b>\n"
        "1. Подключи меня в <b>Telegram Business → Чат-боты</b> (настройки профиля).\n"
        "2. Напиши /faier и укажи @username друга.\n"
        "3. Друг получит приглашение. После принятия начнётся серия.\n"
        "4. Каждый день <b>оба</b> должны написать хотя бы одно сообщение до 00:00 ЕКБ.\n"
        "5. Если кто-то не написал — огонёк станет серым ⚪, напоминания каждые 2 часа.\n"
        "6. Восстановление: 3 раза бесплатно, потом 25 ⭐. Через неделю — окончательно сгорает.\n\n"
        "⚠️ <b>Важно:</b> без подключения в Business-режиме я не смогу видеть сообщения в твоих чатах.\n"
        "Настройки → Telegram Business → Чат-боты → добавь меня."
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("❌ Команда работает только в личных сообщениях.")
        return

    # Проверяем, есть ли уже серия
    existing = db.get_streak_by_chat(chat.id)
    if existing:
        await update.message.reply_text(
            f"🔥 У вас уже есть серия с этим собеседником: <b>{existing['streak']} дней</b>.",
            parse_mode="HTML"
        )
        return

    PENDING_INVITES[user.id] = True
    await update.message.reply_text(
        "👤 Введи @username друга, с которым хочешь растить огонёк:\n"
        "(например: @ivan_petrov)"
    )


async def handle_username_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if not PENDING_INVITES.get(user.id):
        return

    if not text.startswith("@") or len(text) < 2:
        await update.message.reply_text("❌ Введи корректный @username (начинается с @).")
        return

    target_username = text[1:]
    PENDING_INVITES.pop(user.id, None)

    # Сохраняем приглашение
    conn = db.get_conn()
    conn.execute("""
        INSERT INTO pending_invites (from_user_id, from_username, to_user_id)
        VALUES (?, ?, ?)
    """, (user.id, user.username or user.first_name, 0))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Приглашение для @{target_username} создано.\n\n"
        "⚠️ <b>Важно:</b> твой друг должен сам написать мне /start и переслать сюда любое своё сообщение (или нажать кнопку ниже после того, как напишет мне).\n\n"
        "Попроси друга написать мне /start, а затем перешли его сообщение в этот чат — я привяжу его аккаунт.",
        parse_mode="HTML"
    )


# ========== BUSINESS CONNECTION ==========

async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь подключил/отключил бота."""
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)
        logger.info(f"Business connection saved: user={conn.user.id}, connection={conn.id}")
    else:
        logger.info(f"Business connection disabled: user={conn.user.id}")


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сообщение в подключённом чате."""
    msg = update.business_message
    if not msg:
        return

    chat_id = msg.chat.id
    from_user = msg.from_user.id
    connection_id = msg.business_connection_id

    # Проверяем, есть ли серия для этого чата
    streak = db.get_streak_by_chat(chat_id)
    if not streak:
        # Возможно, это первый ответ на приглашение
        return

    # Отмечаем, что пользователь написал
    db.mark_user_wrote(chat_id, from_user)

    # Пробуем продлить серию
    new_streak = db.try_advance_streak(chat_id)
    if new_streak:
        # Серия продлена — проверяем рубежи
        if new_streak in MILESTONES:
            text = (
                f"🎉🔥 <b>ПОЗДРАВЛЯЕМ!</b>\n\n"
                f"Ваша серия достигла <b>{new_streak} дней</b>!\n"
                f"Вы очень дружные и активные! Так держать! 💪✨"
            )
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                    business_connection_id=connection_id
                )
            except Exception as e:
                logger.error(f"Failed to send milestone: {e}")


# ========== ПРИНЯТИЕ ПРИГЛАШЕНИЯ ==========

async def handle_invite_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("accept_"):
        # Логика: инициатор и принявший создают серию
        # В реальности нужен обмен user_id
        await query.edit_message_text(
            "✅ <b>Серия начата! День 1.</b>\n\n"
            "Продолжайте общаться каждый день 🔥\n"
            "Не забудьте: оба должны написать хотя бы по одному сообщению до 00:00 ЕКБ.",
            parse_mode="HTML"
        )
    elif data.startswith("decline_"):
        await query.edit_message_text("❌ Приглашение отклонено.")


# ========== ФОНОВЫЕ ЗАДАЧИ ==========

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    """00:00 ЕКБ — проверка, все ли продлили."""
    logger.info("Running midnight check...")
    streaks = db.get_all_streaks()

    for s in streaks:
        if s["is_gray"] == 1:
            continue

        today = datetime.now(TZ_EKB).date().isoformat()
        last_active = s["last_active_date"]

        # Если последняя активность не сегодня — значит не продлили
        if last_active != today:
            db.set_gray(s["chat_id"])

            text = (
                "⚪ <b>Огонёк стал серым.</b>\n\n"
                "Пора продлить серию! Напишите оба хотя бы по одному сообщению до 00:00 по Екатеринбургу.\n"
                "🔥 Продлите — и огонёк снова загорится!"
            )
            try:
                conn_id = s["business_connection_id"]
                if conn_id:
                    await context.bot.send_message(
                        chat_id=s["chat_id"],
                        text=text,
                        parse_mode="HTML",
                        business_connection_id=conn_id
                    )
            except Exception as e:
                logger.error(f"Failed to send gray notice: {e}")


async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    """Каждые 2 часа напоминаем про серые огоньки."""
    logger.info("Running gray reminder...")
    streaks = db.get_gray_streaks()

    for s in streaks:
        gray_since = s["gray_since"]
        if not gray_since:
            continue

        # Проверяем, не прошло ли больше недели
        gray_dt = datetime.fromisoformat(gray_since)
        now = datetime.now(TZ_EKB)
        if now - gray_dt > timedelta(days=7):
            # Окончательно сгорел — удаляем
            conn = db.get_conn()
            conn.execute("DELETE FROM streaks WHERE chat_id = ?", (s["chat_id"],))
            conn.commit()
            conn.close()
            try:
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text="💀 <b>Серия окончательно сгорела.</b>\n\nПрошла неделя. Начните заново с /faier.",
                    parse_mode="HTML",
                    business_connection_id=s["business_connection_id"]
                )
            except:
                pass
            continue

        # Обычное напоминание
        text = (
            "⚪ <b>Напоминание:</b> огонёк серый.\n\n"
            "Продлите серию — напишите оба! 🔥"
        )
        try:
            await context.bot.send_message(
                chat_id=s["chat_id"],
                text=text,
                parse_mode="HTML",
                business_connection_id=s["business_connection_id"]
            )
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


# ========== ВОССТАНОВЛЕНИЕ ЗА ЗВЁЗДЫ ==========

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Восстановление серии: 3 бесплатно, потом 25 звёзд."""
    chat = update.effective_chat
    if chat.type != "private":
        return

    streak = db.get_streak_by_chat(chat.id)
    if not streak or streak["is_gray"] == 0:
        await update.message.reply_text("❌ У вас нет серого огонька для восстановления.")
        return

    if streak["restore_count"] < 3:
        # Бесплатное восстановление
        success = db.restore_streak(chat.id, paid=False)
        if success:
            await update.message.reply_text(
                "🔥 <b>Огонёк восстановлен!</b>\n\n"
                f"Бесплатных восстановлений осталось: {2 - streak['restore_count']}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Не удалось восстановить.")
    else:
        # Платное восстановление за 25 звёзд
        price = 25
        await context.bot.send_invoice(
            chat_id=chat.id,
            title="Восстановление огонька",
            description=f"Восстановить серию {streak['streak']} дней за {price} звёзд",
            payload=f"restore_{chat.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Восстановление", amount=price)]
        )


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload

    if payload.startswith("restore_"):
        chat_id = int(payload.split("_")[1])
        db.restore_streak(chat_id, paid=True)
        await update.message.reply_text(
            "🔥 <b>Огонёк восстановлен за 25 ⭐!</b>\n\nСерия продолжается!",
            parse_mode="HTML"
        )


# ========== ЗАПУСК ==========

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    app.add_handler(CommandHandler("restore", restore_command))

    # Ввод username
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_username_input))

    # Business
    # ИСПРАВЛЕНИЕ: используем BusinessConnectionHandler и правильный фильтр
    app.add_handler(BusinessConnectionHandler(handle_business_connection))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))

    # Callback
    app.add_handler(CallbackQueryHandler(handle_invite_response))

    # Платежи
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Планировщик
    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
