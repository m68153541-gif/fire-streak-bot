import os
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, BusinessConnectionHandler
)

import database as db

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
URL = os.environ.get("RENDER_EXTERNAL_URL")
TZ_EKB = ZoneInfo("Asia/Yekaterinburg")

# Инициализация бота
bot_app = Application.builder().token(TOKEN).build()
api_app = FastAPI()

# --- ХЕНДЛЕРЫ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Кнопка для запуска Mini App
    web_app_url = f"{URL}/webapp/index.html"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Открыть моего Огонька", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton("📖 Инструкция", callback_data="help")]
    ])
    
    await update.message.reply_text(
        f"🔥 <b>Привет, {user.first_name}!</b>\n\n"
        "Нажми на кнопку ниже, чтобы открыть Mini App и начать!",
        parse_mode="HTML",
        reply_markup=keyboard
    )

async def on_business_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn.is_enabled:
        db.save_connection(conn.user.id, conn.id)

# Регистрируем хендлеры в боте
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(BusinessConnectionHandler(on_business_connect))

# --- WEBHOOK ДЛЯ TELEGRAM ---

@api_app.post(f"/{TOKEN}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.update_queue.put(update)
    return {"status": "ok"}

# --- API ДЛЯ MINI APP ---

@api_app.get("/api/get_partners")
async def get_partners(user_id: int):
    partners = db.get_partners(user_id)
    # Для прототипа просто возвращаем ID
    return {"partners": partners}

@api_app.get("/api/get_fire")
async def get_fire(user_id: int, partner_id: int):
    data = db.get_fire_data(user_id, partner_id)
    return {"fire": data}

@api_app.post("/api/update_fire")
async def update_fire(request: Request):
    data = await request.json()
    user_id = data['user_id']
    partner_id = data['partner_id']
    fire = data['fire']
    action = data['action']
    
    # Сохраняем в базу
    db.save_fire_data(
        user_id, partner_id, 
        fire['name'], fire['hat'], fire['scarf'], fire['shirt']
    )
    
    # Отправляем уведомление в чат с собеседником
    conn_id = db.get_connection(user_id)
    if conn_id:
        try:
            user = await bot_app.bot.get_chat(user_id)
            username = user.username or user.first_name
            text = f"🔥 <b>@{username}</b> {action}.\nПосмотреть: @fier_zyza_bot"
            
            await bot_app.bot.send_message(
                chat_id=partner_id,
                text=text,
                parse_mode="HTML",
                business_connection_id=conn_id
            )
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
    
    return {"status": "ok"}

# --- СТАТИКА И ЗАПУСК ---

api_app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")

# Инициализация БД и бота при старте
@api_app.on_event("startup")
async def startup():
    db.init_db()
    await bot_app.initialize()
    await bot_app.bot.set_webhook(f"{URL}/{TOKEN}", drop_pending_updates=True)
    await bot_app.start()

@api_app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(api_app, host="0.0.0.0", port=port)
