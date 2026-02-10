import os
import asyncio
import logging
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Update
from fastapi import FastAPI, Request
import uvicorn
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# الإعدادات
TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_FILE = "users.json" # قاعدة بيانات بسيطة

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
scheduler = AsyncIOScheduler()

# وظيفة لحفظ بيانات المستخدمين
def save_user(chat_id, lat, lon):
    users = load_users()
    users[str(chat_id)] = {"lat": lat, "lon": lon, "last_notified": ""}
    with open(DB_FILE, "w") as f:
        json.dump(users, f)

def load_users():
    if not os.path.exists(DB_FILE): return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)

# وظيفة لفحص مواقيت الصلاة وإرسال التنبيهات
async def check_prayer_times():
    users = load_users()
    now = datetime.now().strftime("%H:%M")
    
    async with aiohttp.ClientSession() as session:
        for chat_id, data in users.items():
            url = f"http://api.aladhan.com/v1/timings?latitude={data['lat']}&longitude={data['lon']}&method=4"
            async with session.get(url) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    timings = res['data']['timings']
                    
                    # الصلوات التي نريد التنبيه لها
                    prayers = {
                        "Fajr": "الفجر",
                        "Dhuhr": "الظهر",
                        "Asr": "العصر",
                        "Maghrib": "المغرب",
                        "Isha": "العشاء"
                    }
                    
                    for key, name in prayers.items():
                        if timings[key] == now and data.get("last_notified") != f"{key}_{now}":
                            await bot.send_message(chat_id, f"🔔 حان الآن موعد أذان {name} حسب موقعك المحلي.")
                            # تحديث لمنع تكرار التنبيه في نفس الدقيقة
                            users[chat_id]["last_notified"] = f"{key}_{now}"
                            with open(DB_FILE, "w") as f:
                                json.dump(users, f)

# --- معالجة الرسائل ---
@dp.message(F.location)
async def handle_location(message: types.Message):
    lat = message.location.latitude
    lon = message.location.longitude
    save_user(message.chat.id, lat, lon)
    await message.answer("✅ تم تفعيل التنبيهات التلقائية لموقعك بنجاح!")

# --- إعدادات السيرفر ---
@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(f"{RENDER_URL}/webhook")
    # تشغيل فحص الصلاة كل دقيقة
    scheduler.add_job(check_prayer_times, "interval", minutes=1)
    scheduler.start()

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.get("/")
async def index(): return {"status": "running"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)