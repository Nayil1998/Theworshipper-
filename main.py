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

# مدد الإقامة العادية (بالدقائق)
OFFSETS = {
    "Fajr": 25, "Dhuhr": 20, "Asr": 20, "Maghrib": 10, "Isha": 20
}

PRAYER_NAMES = {
    "Fajr": "الفجر", "Dhuhr": "الظهر", "Asr": "العصر", "Maghrib": "المغرب", "Isha": "العشاء"
}

QURAN_VERSES = {
    0: "﴿وَبِالْأَسْحَارِ هُمْ يَسْتَغْفِرُونَ﴾",
    3: "﴿وَالصُّبْحِ إِذَا تَنَفَّسَ﴾",
    6: "﴿إِنَّ قُرْآنَ الْفَجْرِ كَانَ مَشْهُودًا﴾",
    9: "﴿وَتَزَوَّدُوا فَإِنَّ خَيْرَ الزَّادِ التَّقْوَى﴾",
    12: "﴿أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ﴾",
    15: "﴿وَاسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ﴾",
    18: "﴿وَاذْكُر رَّبَّكَ كَثِيرًا وَسَبِّحْ بِالْعَشِيِّ وَالْإِبْكَارِ﴾",
    21: "﴿وَمِنَ اللَّيْلِ فَسَبِّحْهُ وَأَدْبَارَ السُّجُودِ﴾"
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
scheduler = AsyncIOScheduler()

# --- وظائف مساعدة ---
def load_users():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_users(users):
    with open(DB_FILE, "w") as f: json.dump(users, f, indent=4)

def add_minutes(time_str, minutes):
    t = datetime.strptime(time_str, "%H:%M")
    return (t + timedelta(minutes=minutes)).strftime("%H:%M")

async def update_all_prayer_times():
    users = load_users()
    async with aiohttp.ClientSession() as session:
        for chat_id, info in users.items():
            try:
                # تقويم أم القرى (Method 4)
                url = f"http://api.aladhan.com/v1/timings?latitude={info['lat']}&longitude={info['lon']}&method=4"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        timings = data['data']['timings']
                        info['timings'] = timings
                        info['timezone'] = data['data']['meta']['timezone']
                        users[chat_id] = info
            except Exception as e:
                logging.error(f"Error updating times for {chat_id}: {e}")
    save_users(users)

# --- المحرك الرئيسي للتنبيهات ---
async def check_notifications():
    users = load_users()
    for chat_id, info in users.items():
        if 'timings' not in info: continue

        try:
            tz = pytz.timezone(info.get('timezone', 'Asia/Riyadh'))
            now = datetime.now(tz)
            current_time = now.strftime("%H:%M")
            is_friday = now.weekday() == 4
            timings = info['timings']

            for p_en, p_ar in PRAYER_NAMES.items():
                adhan_time = timings[p_en]
                iqamah_time = add_minutes(adhan_time, OFFSETS[p_en])
                display_name = "صلاة الجمعة" if p_en == "Dhuhr" and is_friday else p_ar

                # 1. تنبيه الأذان
                if current_time == adhan_time:
                    if info.get("last_adhan") != f"{p_en}_{current_time}":
                        msg = f"🕌 حان الآن وقت أذان {display_name}\n"
                        msg += f"تقام الصلاة بعد {OFFSETS[p_en]} دقيقة بإذن الله."
                        
                        await bot.send_message(chat_id, msg)
                        info["last_adhan"] = f"{p_en}_{current_time}"
                        save_users(users)

                # 2. تنبيه الإقامة
                elif current_time == iqamah_time:
                    if info.get("last_iqamah") != f"{p_en}_{current_time}":
                        # في الجمعة لا يوجد تنبيه إقامة معتاد بعد الأذان
                        if not (is_friday and p_en == "Dhuhr"):
                            await bot.send_message(chat_id, f"📢 حان الآن وقت إقامة {display_name}.. أقبل على صلاتك بخشوع.")

                        info["last_iqamah"] = f"{p_en}_{current_time}"
                        save_users(users)

        except Exception as e:
            logging.error(f"Notification error for {chat_id}: {e}")

# --- معالجات الرسائل ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 مشاركة الموقع لتحديث الأوقات", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer(
        "مرحباً بك في بوت أوقات الصلاة 🕌\n\n"
        "سيقوم البوت بتنبيهك بـ:\n"
        "✅ مواعيد الأذان حسب موقعك.\n"
        "✅ تنبيه قبل الإقامة.\n"
        "✅ أذكار وآيات قرآنية يومية.\n\n"
        "يرجى الضغط على الزر بالأسفل لمشاركة موقعك وتفعيل التنبيهات:",
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
    await message.answer("✅ تم تفعيل موقعك بنجاح! ستصلك التنبيهات بناءً على تقويم أم القرى.")
    await update_all_prayer_times()

# --- وظائف دورية ---
async def send_verse():
    users = load_users()
    hour = (datetime.now().hour // 3) * 3
    verse = QURAN_VERSES.get(hour, QURAN_VERSES[12])
    for chat_id in users:
        try: await bot.send_message(chat_id, f"📖 {verse}")
        except: pass

async def send_daily_adhkar(title):
    users = load_users()
    for chat_id in users:
        try: await bot.send_message(chat_id, title)
        except: pass

# --- Webhook و FastAPI ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    if RENDER_URL: await bot.set_webhook(f"{RENDER_URL}/webhook")

    # تحديث أوقات الصلاة كل 12 ساعة
    scheduler.add_job(update_all_prayer_times, "interval", hours=12)
    # فحص التنبيهات كل 30 ثانية
    scheduler.add_job(check_notifications, "interval", seconds=30)
    # آية كل 3 ساعات
    scheduler.add_job(send_verse, "interval", hours=3)
    # الأذكار
    scheduler.add_job(send_daily_adhkar, "cron", hour=5, minute=0, args=["☀️ أذكار الصباح | حفظك الله في يومك."])
    scheduler.add_job(send_daily_adhkar, "cron", hour=17, minute=0, args=["🌙 أذكار المساء | أنس ليلك بذكر الله."])

    scheduler.start()
    await update_all_prayer_times()

@app.get("/")
async def index(): return {"status": "Bot is Running"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)