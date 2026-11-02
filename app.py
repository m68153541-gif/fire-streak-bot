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

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

# Хранилище состояний в оперативной памяти
USER_STATES = {}

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Я — бот для поддержания серии огонька.\n\n"
        "📌 <b>Инструкция:</b>\n"
        "1. Подключи меня в <b>Настройки → Telegram Business → Чат-боты</b>.\n"
        "2. Напиши /faier.\n"
        "3. Узнай числовой ID друга (через @userinfobot) и отправь мне.\n"
        "4. Друг получит приглашение от твоего имени.\n"
        "5. Каждый день в 00:00 ЕКБ я присылаю кнопку «Поддержать». Жать должны оба.\n\n"
        "⚠️ <b>Важно:</b> без Business-подключения я не смогу писать от твоего имени!"
    )
    await update.message.reply_text(text, parse_mode="HTML")

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

    USER_STATES[user.id] = {"state": "waiting_id"}
    await update.message.reply_text(
        "👤 <b>Шаг 1:</b> Попроси друга узнать свой ID.\n\n"
        "Пусть он напишет боту <b>@userinfobot</b> команду /start. Бот пришлёт ему число (например, <code>123456789</code>).\n\n"
        "Введи это число сюда:",
        parse_mode="HTML"
    )

async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state_data = USER_STATES.get(user.id)
    
    # Если пользователь не в процессе ввода ID — игнорируем
    if not state_data or state_data.get("state") != "waiting_id":
        return

    text = update.message.text.strip()
    
    # Проверяем, что введено число
    if not text.isdigit():
        await update.message.reply_text("❌ Введи числовой ID (только цифры, без @ и букв).")
        return

    target_id = int(text)
    USER_STATES.pop(user.id, None) # Сбрасываем состояние

    conn_id = db.get_connection(user.id)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}_{target_id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{user.id}")]
    ])
    
    try:
        # Отправляем сообщение другу ОТ ИМЕНИ ПОЛЬЗОВАТЕЛЯ
        await context.bot.send_message(
            chat_id=target_id, 
            text=f"🔥 <b>Привет! {user.first_name} предлагает растить огонек дружбы.</b>\n\nНажми кнопку ниже, чтобы начать:",
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=conn_id
        )
        await update.message.reply_text("✅ Приглашение успешно отправлено другу!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки: {e}\n\nУбедитесь, что вы общались с этим пользователем в последние 24 часа.")

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
            provider_token="", 
            currency="XTR",
            prices=[LabeledPrice(label="Восстановление", amount=25)]
        )

# ==================== КНОПКИ (CALLBACK) ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    # 1. Принятие приглашения
    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id = int(parts[1])
        receiver_id = int(parts[2])
        
        chat_id = receiver_id 
        conn_id = db.get_connection(initiator_id)
        
        db.create_streak(chat_id, conn_id, initiator_id, receiver_id)
        
        await query.edit_message_text(
            "🔥 <b>Серия начата! День 1.</b>\n\n"
            "Каждый день в 00:00 ЕКБ я буду присылать сюда кнопку. Нажимать должны оба.",
            parse_mode="HTML"
        )
        
    # 2. Отклонение приглашения
    elif data.startswith("dec_"):
        await query.edit_message_text("❌ Приглашение отклонено.")

    # 3. Продление огонька (кнопка Поддержать)
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
                text = f"🎉 <b>ПОЗДРАВЛЯЕМ! Серия достигла {new_streak} дней!</b>\nВы очень дружные! 💪"
            
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML",
                business_connection_id=streak["connection_id_a"]
            )

    # 4. Восстановление из напоминания (кнопка Восстановить)
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
            await query.message.reply_text("❌ Бесплатные попытки закончились. Используйте /restore для оплаты звездами.")

# ==================== ПЛАТЕЖИ ====================

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if payment.invoice_payload.startswith("restore_"):
        chat_id = int(payment.invoice_payload.split("_")[1])
        db.restore_streak(chat_id, paid=True)
        await update.message.reply_text("🔥 <b>Огонек восстановлен за 25 ⭐!</b>", parse_mode="HTML")

# ==================== ФОНОВЫЕ ЗАДАЧИ (CRON) ====================

async def check_midnight(context: ContextTypes.DEFAULT_TYPE):
    """00:00 ЕКБ - проверяем, кто не продлил, делаем огонь серым и кидаем кнопку"""
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
            logger.error(f"Failed midnight check for {s['chat_id']}: {e}")

async def remind_gray(context: ContextTypes.DEFAULT_TYPE):
    """Каждые 2 часа проверяем серые огоньки и шлем напоминания"""
    streaks = db.get_unsent_reminders()
    for s in streaks:
        # 1. Проверка на сгорание (7 дней)
        if not s["gray_since"]:
            continue
            
        gray_since = datetime.fromisoformat(s["gray_since"])
        if datetime.now(TZ_EKB) - gray_since > timedelta(days=7):
            db.kill_streak(s["chat_id"])
            try:
                await context.bot.send_message(
                    chat_id=s["chat_id"],
                    text="💀 <b>Серия окончательно сгорела.</b>\n\nПрошло 7 дней. Начните заново с /faier.",
                    parse_mode="HTML",
                    business_connection_id=s["connection_id_a"]
                )
            except Exception as e:
                logger.error(f"Failed kill streak: {e}")
            continue

        # 2. Обычное напоминание (если не сгорел)
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
            logger.error(f"Failed remind gray: {e}")

# ==================== BUSINESS CONNECTION ====================

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)
        logger.info(f"✅ Business connection saved: user={conn.user.id}")
    else:
        logger.info(f"❌ Business connection disabled: user={conn.user.id}")

# ==================== ЗАПУСК ====================

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN is missing!")

    db.init_db()
    app = Application.builder().token(TOKEN).build()

    # Хендлеры команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faier", faier))
    app.add_handler(CommandHandler("restore", restore_command))
    
    # Хендлеры текста и кнопок
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_message_input))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Бизнес и платежи
    app.add_handler(BusinessConnectionHandler(on_business_connect))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Планировщик задач
    jq = app.job_queue
    jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
    jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

    # Запуск Webhook на Render с защитой от падений (bootstrap_retries)
    PORT = int(os.environ.get("PORT", 10000))
    URL = os.environ.get("RENDER_EXTERNAL_URL")
    
    if not URL:
        logger.error("❌ RENDER_EXTERNAL_URL is not set!")
        return

    logger.info(f"🚀 Starting webhook on {URL}")
    app.run_webhook(
        listen="0.0.0.0", 
        port=PORT, 
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}", 
        drop_pending_updates=True,
        bootstrap_retries=5  # <--- Защита от Flood Control
    )

if __name__ == "__main__":
    main()
