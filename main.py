import os
import asyncio
import json
import logging
import aiohttp
import pytz
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Update, ReplyKeyboardMarkup, KeyboardButton
from fastapi import FastAPI, Request
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- الإعدادات ---
TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_FILE = "/tmp/users.json"

# إعدادات الإقامة (بالدقائق بعد الأذان)
IQAMAH_OFFSETS = {
    "Fajr": 25,
    "Dhuhr": 20,
    "Asr": 20,
    "Maghrib": 10,
    "Isha": 20
}

PRAYER_NAMES = {
    "Fajr": "الفجر",
    "Dhuhr": "الظهر",
    "Asr": "العصر",
    "Maghrib": "المغرب",
    "Isha": "العشاء"
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
scheduler = AsyncIOScheduler()

# --- وظائف قاعدة البيانات ---
def load_users():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading DB: {e}")
            return {}
    return {}

def save_users(users):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(users, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving DB: {e}")

# --- وظائف الوقت ---
def add_minutes(time_str, minutes):
    """يضيف دقائق لوقت بصيغة HH:MM ويعيده بنفس الصيغة"""
    t = datetime.strptime(time_str, "%H:%M")
    new_t = t + timedelta(minutes=minutes)
    return new_t.strftime("%H:%M")

# --- محرك التنبيهات الرئيسي ---
async def check_and_send_notifications():
    users = load_users()
    if not users:
        return

    async with aiohttp.ClientSession() as session:
        for chat_id, info in users.items():
            try:
                # جلب مواقيت الصلاة
                url = f"http://api.aladhan.com/v1/timings?latitude={info['lat']}&longitude={info['lon']}&method=4"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    
                    data = await resp.json()
                    timings = data['data']['timings']
                    user_tz = pytz.timezone(data['data']['meta']['timezone'])
                    now_local = datetime.now(user_tz).strftime("%H:%M")
                    
                    updated = False

                    for p_en, p_ar in PRAYER_NAMES.items():
                        adhan_time = timings[p_en]
                        iqamah_time = add_minutes(adhan_time, IQAMAH_OFFSETS.get(p_en, 20))

                        # 1. تنبيه الأذان
                        if adhan_time == now_local:
                            last_notified = info.get("last_adhan", "")
                            if last_notified != f"{p_en}_{now_local}":
                                await bot.send_message(
                                    chat_id, 
                                    f"🔔 حان الآن موعد أذان {p_ar}\n"
                                    f"⏰ تقام الصلاة بعد {IQAMAH_OFFSETS.get(p_en)} دقيقة (عند {iqamah_time}).\n\n"
                                    f"لا تنسَ تردد الأذان والدعاء المستجاب بين الأذان والإقامة."
                                )
                                info["last_adhan"] = f"{p_en}_{now_local}"
                                updated = True

                        # 2. تنبيه الإقامة
                        elif iqamah_time == now_local:
                            last_iqamah = info.get("last_iqamah", "")
                            if last_iqamah != f"{p_en}_{now_local}":
                                await bot.send_message(
                                    chat_id, 
                                    f"🕌 حان الآن وقت إقامة صلاة {p_ar}.\n"
                                    f"استووا واعتدلوا، أقم صلاتك تنعم بحياتك."
                                )
                                info["last_iqamah"] = f"{p_en}_{now_local}"
                                updated = True

                    if updated:
                        users[chat_id] = info
                        save_users(users)

            except Exception as e:
                logging.error(f"Error checking for user {chat_id}: {e}")

# --- معالجات الرسائل ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 تفعيل التنبيهات (إرسال الموقع)", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "🌙 مرحباً بك في بوت مواقيت الصلاة المطور!\n\n"
        "سأقوم بتنبيهك عند:\n"
        "1. وقت الأذان مباشرة.\n"
        "2. وقت الإقامة (حسب توقيت مساجد منطقتك التقريبي).\n\n"
        "يرجى الضغط على الزر أدناه لمشاركة موقعك:",
        reply_markup=kb
    )

@dp.message(F.location)
async def handle_location(message: types.Message):
    users = load_users()
    users[str(message.chat.id)] = {
        "lat": message.location.latitude,
        "lon": message.location.longitude,
        "last_adhan": "",
        "last_iqamah": ""
    }
    save_users(users)
    await message.answer(
        "✅ تم تفعيل التنبيهات بنجاح!\n\n"
        "سأرسل لك تنبيه الأذان، ثم تنبيه الإقامة بعده بـ 20 دقيقة (أو حسب الصلاة).\n"
        "يمكنك دائماً تحديث موقعك بإعادة إرساله."
    )

# --- إعدادات FastAPI و Webhook ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    if RENDER_URL:
        await bot.set_webhook(f"{RENDER_URL}/webhook")
    
    # تشغيل الفحص كل 30 ثانية لضمان الدقة في وقت الإقامة والأذان
    scheduler.add_job(check_and_send_notifications, "interval", seconds=30)
    scheduler.start()

@app.get("/")
async def index():
    return {"status": "Bot is active", "scheduler": "Running"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)