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

# آيات قرآنية حسب الوقت (كل 3 ساعات)
QURAN_VERSES = {
    0: "﴿وَبِالْأَسْحَارِ هُمْ يَسْتَغْفِرُونَ﴾. طوبى للمستغفرين.",
    3: "﴿وَالصُّبْحِ إِذَا تَنَفَّسَ﴾. صباح الطاعة والرضا.",
    6: "﴿إِنَّ قُرْآنَ الْفَجْرِ كَانَ مَشْهُودًا﴾. ذكر الله أول النهار فلاح.",
    9: "﴿وَتَزَوَّدُوا فَإِنَّ خَيْرَ الزَّادِ التَّقْوَى﴾.",
    12: "﴿أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ﴾.",
    15: "﴿وَاسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ﴾. اقترب الإفطار، صبراً واحتساباً.",
    18: "﴿ثُمَّ أَتِمُّوا الصِّيَامَ إِلَى اللَّيْلِ﴾. هنيئاً لك الإفطار، تقبل الله صيامك.",
    21: "﴿إِنَّا أَنْزَلْنَاهُ فِي لَيْلَةِ الْقَدْرِ﴾. أنس ليلك بالقرآن والقيام."
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
            with open(DB_FILE, "r") as f: return json.load(f)
        except Exception as e:
            logging.error(f"Error loading DB: {e}")
            return {}
    return {}

def save_users(users):
    try:
        with open(DB_FILE, "w") as f: json.dump(users, f, indent=4)
    except Exception as e: logging.error(f"Error saving DB: {e}")

def add_minutes(time_str, minutes):
    t = datetime.strptime(time_str, "%H:%M")
    return (t + timedelta(minutes=minutes)).strftime("%H:%M")

# --- محرك التنبيهات الرئيسي ---
async def check_and_send_notifications():
    users = load_users()
    if not users: return

    async with aiohttp.ClientSession() as session:
        for chat_id, info in users.items():
            try:
                url = f"http://api.aladhan.com/v1/timings?latitude={info['lat']}&longitude={info['lon']}&method=4"
                async with session.get(url) as resp:
                    if resp.status != 200: continue
                    
                    data = await resp.json()
                    timings = data['data']['timings']
                    user_tz = pytz.timezone(data['data']['meta']['timezone'])
                    now_dt = datetime.now(user_tz)
                    now_local = now_dt.strftime("%H:%M")
                    is_friday = now_dt.weekday() == 4 # 4 تعني يوم الجمعة

                    updated = False
                    for p_en, p_ar in PRAYER_NAMES.items():
                        # تعديل صلاة الجمعة
                        display_name = "صلاة الجمعة" if p_en == "Dhuhr" and is_friday else p_ar
                        
                        adhan_time = timings[p_en]
                        iqamah_time = add_minutes(adhan_time, IQAMAH_OFFSETS.get(p_en, 20))

                        # 1. تنبيه الأذان
                        if adhan_time == now_local:
                            if info.get("last_adhan") != f"{p_en}_{now_local}":
                                msg = f"🌙 حان الآن أذان {display_name}\n"
                                if p_en == "Maghrib": msg += "ذهب الظمأ وابتلت العروق، تقبل الله منك."
                                elif p_en == "Fajr": msg += "صوماً مقبولاً، بادر بالصلاة."
                                else: msg += f"تقام الصلاة بعد {IQAMAH_OFFSETS.get(p_en)} دقيقة."
                                
                                await bot.send_message(chat_id, msg)
                                info["last_adhan"] = f"{p_en}_{now_local}"
                                updated = True

                        # 2. تنبيه الإقامة
                        elif iqamah_time == now_local:
                            if info.get("last_iqamah") != f"{p_en}_{now_local}":
                                await bot.send_message(chat_id, f"🕌 إقامة {display_name}.. أقبل على صلاتك بخشوع.")
                                info["last_iqamah"] = f"{p_en}_{now_local}"
                                updated = True

                    if updated:
                        users[chat_id] = info
                        save_users(users)
            except Exception as e: logging.error(f"Error for user {chat_id}: {e}")

# --- وظائف التذكير الإضافية ---
async def send_periodic_verse():
    users = load_users()
    current_hour = datetime.now().hour
    closest_hour = (current_hour // 3) * 3
    verse = QURAN_VERSES.get(closest_hour, QURAN_VERSES[12])
    for chat_id in users:
        try: await bot.send_message(chat_id, f"📖 {verse}")
        except: pass

async def send_adhkar(msg):
    users = load_users()
    for chat_id in users:
        try: await bot.send_message(chat_id, msg)
        except: pass

# --- معالجات الرسائل ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 تفعيل تنبيهات رمضان", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer(
        "مبارك عليك شهر رمضان 🌙\n\n"
        "سأقوم بتنبيهك للأذان والإقامة، وأذكار الصباح والمساء، وآيات قرآنية دورية.\n"
        "يرجى مشاركة موقعك للبدء:",
        reply_markup=kb
    )

@dp.message(F.location)
async def handle_location(message: types.Message):
    users = load_users()
    users[str(message.chat.id)] = {
        "lat": message.location.latitude,
        "lon": message.location.longitude,
        "last_adhan": "", "last_iqamah": ""
    }
    save_users(users)
    await message.answer("✅ تم التفعيل بنجاح. جعلنا الله وإياكم من المقبولين.")

# --- إعدادات FastAPI و Webhook ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    if RENDER_URL: await bot.set_webhook(f"{RENDER_URL}/webhook")
    
    # الجدولة
    scheduler.add_job(check_and_send_notifications, "interval", seconds=30)
    scheduler.add_job(send_periodic_verse, "interval", hours=3)
    scheduler.add_job(send_adhkar, "cron", hour=5, minute=30, args=["☀️ أذكار الصباح | حصن صيامك ويومك."])
    scheduler.add_job(send_adhkar, "cron", hour=17, minute=0, args=["🌙 أذكار المساء | أنس ليلك بذكر ربك."])
    
    scheduler.start()

@app.get("/")
async def index(): return {"status": "Ramadan Bot is active"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)