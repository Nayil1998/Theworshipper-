import os
import asyncio
import json
import logging
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Update
from fastapi import FastAPI, Request
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- الإعدادات ---
TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_FILE = "/tmp/users.json"  # في Render نستخدم /tmp للتخزين المؤقت

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
scheduler = AsyncIOScheduler()

# --- إدارة البيانات ---
def load_users():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_user(chat_id, lat, lon):
    users = load_users()
    users[str(chat_id)] = {"lat": lat, "lon": lon, "last_notified": ""}
    with open(DB_FILE, "w") as f:
        json.dump(users, f)

# --- وظيفة إرسال التنبيهات ---
async def check_and_send_notifications():
    users = load_users()
    now = datetime.now().strftime("%H:%M")
    
    async with aiohttp.ClientSession() as session:
        for chat_id, info in users.items():
            try:
                # طلب المواقيت بناءً على موقع المستخدم
                url = f"http://api.aladhan.com/v1/timings?latitude={info['lat']}&longitude={info['lon']}&method=4"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        timings = data['data']['timings']
                        
                        prayers = {"Fajr": "الفجر", "Dhuhr": "الظهر", "Asr": "العصر", "Maghrib": "المغرب", "Isha": "العشاء"}
                        
                        for p_en, p_ar in prayers.items():
                            p_time = timings[p_en]
                            # إذا تطابق الوقت ولم نرسل تنبيهاً في هذه الدقيقة
                            if p_time == now and info.get("last_notified") != f"{p_en}_{now}":
                                await bot.send_message(chat_id, f"🔔 حان الآن موعد أذان {p_ar} حسب موقعك.")
                                users[chat_id]["last_notified"] = f"{p_en}_{now}"
                                with open(DB_FILE, "w") as f:
                                    json.dump(users, f)
            except Exception as e:
                print(f"Error for user {chat_id}: {e}")

# --- معالجة الرسائل ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    await message.answer("🌙 مرحباً بك! يرجى إرسال موقعك (Location) لتفعيل تنبيهات الأذان تلقائياً.", 
                         reply_markup=types.ReplyKeyboardMarkup(
                             keyboard=[[types.KeyboardButton(text="📍 إرسال الموقع", request_location=True)]],
                             resize_keyboard=True))

@dp.message(F.location)
async def handle_location(message: types.Message):
    save_user(message.chat.id, message.location.latitude, message.location.longitude)
    await message.answer("✅ تم حفظ موقعك بنجاح! سأقوم بتنبيهك عند كل صلاة.")

# --- إعدادات السيرفر (Render Webhook) ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(f"{RENDER_URL}/webhook")
    scheduler.add_job(check_and_send_notifications, "interval", minutes=1)
    scheduler.start()

@app.get("/")
async def index():
    return {"status": "Bot is Running"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)