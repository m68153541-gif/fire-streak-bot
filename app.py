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
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

# ==================== БОТ В ЛИЧКЕ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = db.get_connection(user.id)
    
    text = (
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Чтобы начать, выполни два шага:\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Напиши команду <code>/faier</code> прямо в чате с другом, с которым хочешь растить огонек."
    )
    
    if conn_id:
        text = (
            f"✅ <b>Бот подключен к твоему Business!</b>\n\n"
            "Теперь напиши команду <code>/faier</code> прямо в чате с другом, с которым хочешь растить огонек."
        )
        
    await update.message.reply_text(text, parse_mode="HTML")

# ==================== КОМАНДА /faier В ЧАТЕ С ДРУГОМ ====================

async def faier_in_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, что это Business-сообщение (команда в чате с другом)
    msg = update.business_message
    if not msg:
        return

    chat_id = msg.chat.id
    conn_id = msg.business_connection_id
    
    # Находим владельца подключения
    conn = db.get_conn()
    row = conn.execute("SELECT user_id FROM user_connections WHERE connection_id = ?", (conn_id,)).fetchone()
    conn.close()
    
    if not row:
        return
        
    owner_id = row["user_id"]

    # Если серия уже есть — сообщаем
    existing = db.get_streak(chat_id)
    if existing:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔥 У вас уже есть серия: <b>{existing['streak']} дней</b>.",
                parse_mode="HTML",
                business_connection_id=conn_id
            )
        except: pass
        return

    # Удаляем сообщение с командой (если есть права)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except: pass

    # Отправляем приглашение с кнопками
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{owner_id}_{msg.from_user.id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{owner_id}")]
    ])

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔥 <b>Предложение растить огонек!</b>\n\nНажми «Принять», чтобы начать серию.",
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=conn_id
        )
    except Exception as e:
        logger.error(f"Ошибка отправки приглашения: {e}")

# ==================== КНОПКИ ====================

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
        
        await query.edit_message_text(
            "🔥 <b>Серия начата! День 1.</b>\n\n"
            "В 00:00 ЕКБ я пришлю кнопку, чтобы продлить огонек.",
            parse_mode="HTML"
        )
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔥 <b>Наш огонек зажжен! День 1.</b>",
                parse_mode="HTML",
                business_connection_id=conn_id
            )
        except: pass

    elif data.startswith("dec_"):
        await query.edit_message_text("❌ Приглашение отклонено.")

    elif data == "keep_":
        chat_id = query.message.chat.id
        status, result = db.press_button(chat_id, user.id)
        
        if status == "waiting":
            if result == 1:
                await query.answer("✅ Ты поддержал огонек!")
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
                text = f"🎉 <b>ПОЗДРАВЛЯЕМ! Серия достигла {new_streak} дней!</b>"
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
        else:
            # Отправляем счет на 25 звезд
            try:
                await context.bot.send_invoice(
                    chat_id=chat_id,
                    title="Восстановление огонька",
                    description=f"Восстановить серию {streak['streak']} дней за 25 звезд",
                    payload=f"restore_{chat_id}",
                    provider_token="",
                    currency="XTR",
                    prices=[LabeledPrice(label="Восстановление", amount=25)],
                    business_connection_id=streak["connection_id_a"]
                )
            except Exception as e:
                logger.error(f"Ошибка счета: {e}")

# ==================== ПЛАТЕЖИ ====================

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
        
        # Если остались бесплатные восстановления, даем кнопку
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
        db.save_connection(conn.user.id, conn.id)
        logger.info(f"✅ Подключение сохранено: {conn.user.id}")
    else:
        conn_db = db.get_conn()
        conn_db.execute("DELETE FROM user_connections WHERE user_id = ?", (conn.user.id,))
        conn_db.commit()
        conn_db.close()
        logger.info(f"❌ Подключение удалено: {conn.user.id}")

# ==================== ЗАПУСК ====================

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN is missing!")
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    # В личке
    app.add_handler(CommandHandler("start", start))
    
    # В бизнес-чате (команда /faier)
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE & filters.COMMAND, faier_in_chat))
    
    # Остальное
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(BusinessConnectionHandler(on_business_connect))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    logger.info("Bot starting (polling)...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
