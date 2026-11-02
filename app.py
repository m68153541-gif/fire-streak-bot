import os
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, BusinessConnectionHandler, PreCheckoutQueryHandler
)

import database as db

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
URL = os.environ.get("RENDER_EXTERNAL_URL")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")
MILESTONES = [10, 30, 60, 100, 200, 300, 500, 700, 1000]

USER_STATES = {}
bot_app = Application.builder().token(TOKEN).build()
api_app = FastAPI()

# ==================== ХЕНДЛЕРЫ БОТА ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Открыть Огонька", web_app=WebAppInfo(url=f"{URL}/webapp/index.html"))]])
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "1. Подключи меня в Business-режиме.\n"
        "2. Напиши /faier и введи ID друга.",
        parse_mode="HTML", reply_markup=keyboard
    )

async def faier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn_id = db.get_connection(user.id)
    if not conn_id:
        await update.message.reply_text("❌ Бот не подключен в Business-режиме! Переподключи меня в настройках.")
        return

    USER_STATES[user.id] = {"state": "waiting_id"}
    await update.message.reply_text("👤 Введи числовой ID друга:")

async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state_data = USER_STATES.get(user.id)
    if not state_data or state_data.get("state") != "waiting_id": return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Введи числовой ID (только цифры).")
        return

    target_id = int(text)
    USER_STATES.pop(user.id, None)
    conn_id = db.get_connection(user.id)

    # ПРЯМАЯ ОТПРАВКА ПО ID (БЕЗ ПРОВЕРОК)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"acc_{user.id}_{target_id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"dec_{user.id}")]
    ])
    
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🔥 <b>Привет! {user.first_name} предлагает растить огонек.</b>\n\nНажми кнопку ниже:",
            parse_mode="HTML", reply_markup=keyboard, business_connection_id=conn_id
        )
        await update.message.reply_text("✅ Приглашение отправлено!")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await update.message.reply_text(
            f"❌ Ошибка Telegram: {e}\n\n"
            "Если ошибка BUSINESS_PEER_INVALID: попроси друга написать тебе любое сообщение и попробуй снова."
        )

# ==================== БИЗНЕС-СООБЩЕНИЯ (БОЛЬШЕ НЕ БЛОКИРУЮТ) ====================

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg: return
    logger.info(f"📩 Получено бизнес-сообщение от {msg.from_user.id} в чат {msg.chat.id}")

# ==================== КНОПКИ ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("acc_"):
        parts = data.split("_")
        initiator_id, receiver_id = int(parts[1]), int(parts[2])
        chat_id = receiver_id
        conn_id = db.get_connection(initiator_id)
        db.create_streak(chat_id, conn_id, initiator_id, receiver_id)
        await query.edit_message_text("🔥 <b>Серия начата! День 1.</b>", parse_mode="HTML")
        
    elif data.startswith("dec_"):
        await query.edit_message_text("❌ Отклонено.")

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
        elif status == "success":
            new_streak = result
            await query.message.delete()
            streak = db.get_streak(chat_id)
            text = f"🔥 <b>Огонек продлен! Серия: {new_streak} дней.</b>"
            if new_streak in MILESTONES:
                text = f"🎉 <b>ПОЗДРАВЛЯЕМ! Серия достигла {new_streak} дней!</b>"
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", business_connection_id=streak["connection_id_a"])

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
                await context.bot.send_message(chat_id=s["chat_id"], text="💀 <b>Серия сгорела.</b>", parse_mode="HTML", business_connection_id=s["connection_id_a"])
            except: pass
            continue
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Поддержать", callback_data="keep_")],[InlineKeyboardButton("♻️ Восстановить", callback_data=f"restore_{s['chat_id']}")]])
        try:
            await context.bot.send_message(chat_id=s["chat_id"], text="⚪ <b>Огонек серый.</b> Пора продлить!", parse_mode="HTML", reply_markup=keyboard, business_connection_id=s["connection_id_a"])
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

# Регистрация
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("faier", faier))
bot_app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_message_input))
bot_app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))
bot_app.add_handler(CallbackQueryHandler(handle_callback))
bot_app.add_handler(BusinessConnectionHandler(on_business_connect))
bot_app.add_handler(PreCheckoutQueryHandler(pre_checkout))
bot_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

jq = bot_app.job_queue
jq.run_daily(check_midnight, time=time(0, 0, tzinfo=TZ_EKB))
jq.run_repeating(remind_gray, interval=timedelta(hours=2), first=60)

# ==================== FASTAPI ====================

@api_app.post(f"/{TOKEN}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.update_queue.put(update)
    return {"status": "ok"}

@api_app.get("/api/get_partners")
async def get_partners(user_id: int):
    return {"partners": db.get_partners(user_id)}

@api_app.get("/api/get_fire")
async def get_fire(user_id: int, partner_id: int):
    return {"fire": db.get_fire_data(user_id, partner_id)}

@api_app.post("/api/update_fire")
async def update_fire(request: Request):
    data = await request.json()
    db.save_fire_data(data['user_id'], data['partner_id'], data['fire']['name'], data['fire']['hat'], data['fire']['scarf'], data['fire']['shirt'])
    conn_id = db.get_connection(data['user_id'])
    if conn_id:
        try:
            user = await bot_app.bot.get_chat(data['user_id'])
            await bot_app.bot.send_message(
                chat_id=data['partner_id'], text=f"🔥 <b>@{user.username or user.first_name}</b> {data['action']}.\nПосмотреть: @fier_zyza_bot",
                parse_mode="HTML", business_connection_id=conn_id
            )
        except: pass
    return {"status": "ok"}

api_app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")

@api_app.on_event("startup")
async def startup():
    db.init_db()
    await bot_app.initialize()
    await bot_app.bot.set_webhook(
        f"{URL}/{TOKEN}", 
        drop_pending_updates=True,
        allowed_updates=[
            "message",
            "callback_query",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages"
        ]
    )
    await bot_app.start()

@api_app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()

if __name__ == "__main__":
    uvicorn.run(api_app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
