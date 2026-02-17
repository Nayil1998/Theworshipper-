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

# إعدادات الإقامة (بالدقائق)
IQAMAH_OFFSETS = {"Fajr": 25, "Dhuhr": 20, "Asr": 20, "Maghrib": 10, "Isha": 20}

# محتوى الآيات حسب التوقيت (كل 3 ساعات)
QURAN_VERSES = {
    0: "﴿وَبِالْأَسْحَارِ هُمْ يَسْتَغْفِرُونَ﴾ .. وقت السحر غنيمة.",
    3: "﴿وَالصُّبْحِ إِذَا تَنَفَّسَ﴾ .. صباح طاعة وبركة.",
    6: "﴿قُلْ إِنَّ صَلَاتِي وَنُسُكِي وَمَحْيَايَ وَمَمَاتِي لِلَّهِ رَبِّ الْعَالَمِينَ﴾.",
    9: "﴿وَتَزَوَّدُوا فَإِنَّ خَيْرَ الزَّادِ التَّقْوَى﴾.",
    12: "﴿أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ﴾.",
    15: "﴿وَاسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ﴾ .. رمضان مدرسة الصبر.",
    18: "﴿ثُمَّ أَتِمُّوا الصِّيَامَ إِلَى اللَّيْلِ﴾ .. هنيئاً للصائمين.",
    21: "﴿إِنَّا أَنْزَلْنَاهُ فِي لَيْلَةِ الْقَدْرِ﴾ .. ليلك عمار بالقرآن."
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

def get_prayer_name(p_en, is_friday):
    if p_en == "Dhuhr" and is_friday:
        return "صلاة الجمعة"
    names = {"Fajr": "الفجر", "Dhuhr": "الظهر", "Asr": "العصر", "Maghrib": "المغرب", "Isha": "العشاء"}
    return names.get(p_en)

# --- المهام المجدولة ---

# 1. تنبيهات الصلاة (كل دقيقة)
async def check_prayer_notifications():
    users = load_users()
    async with aiohttp.ClientSession() as session:
        for chat_id, info in users.items():
            try:
                url = f"http://api.aladhan.com/v1/timings?latitude={info['lat']}&longitude={info['lon']}&method=4"
                async with session.get(url) as resp:
                    if resp.status != 200: continue
                    data = (await resp.json())['data']
                    timings = data['timings']
                    user_tz = pytz.timezone(data['meta']['timezone'])
                    now = datetime.now(user_tz)
                    now_str = now.strftime("%H:%M")
                    is_friday = now.weekday() == 4

                    for p_en in IQAMAH_OFFSETS.keys():
                        p_ar = get_prayer_name(p_en, is_friday)
                        adhan_t = timings[p_en]
                        iqamah_t = add_minutes(adhan_t, IQAMAH_OFFSETS[p_en])

                        # أذان
                        if adhan_t == now_str and info.get("l_ad") != f"{p_en}_{now_str}":
                            msg = f"🌙 حان أذان {p_ar}\n"
                            if p_en == "Maghrib": msg += "تقبل الله صيامكم، هنيئاً لكم الإفطار."
                            elif p_en == "Fajr": msg += "صوماً مقبولاً، كفوا أيديكم وباشروا صلاتكم."
                            else: msg += f"تقام الصلاة بعد {IQAMAH_OFFSETS[p_en]} دقيقة."
                            
                            await bot.send_message(chat_id, msg)
                            info["l_ad"] = f"{p_en}_{now_str}"
                            save_users(users)

                        # إقامة
                        elif iqamah_t == now_str and info.get("l_iq") != f"{p_en}_{now_str}":
                            await bot.send_message(chat_id, f"🕌 إقامة {p_ar}.. أقبل على صلاتك بخشوع.")
                            info["l_iq"] = f"{p_en}_{now_str}"
                            save_users(users)
            except: continue

# 2. آية كل 3 ساعات
async def send_periodic_verse():
    users = load_users()
    hour = datetime.now().hour
    # تقريب الساعة لأقرب 3 ساعات (0, 3, 6...)
    closest_hour = (hour // 3) * 3
    verse = QURAN_VERSES.get(closest_hour, QURAN_VERSES[12])
    
    for chat_id in users:
        try: await bot.send_message(chat_id, f"📖 {verse}")
        except: pass

# 3. أذكار الصباح والمساء
async def send_adhkar_morning():
    users = load_users()
    for chat_id in users:
        try: await bot.send_message(chat_id, "☀️ أذكار الصباح | حصن صيامك بذكر الله.")
        except: pass

async def send_adhkar_evening():
    users = load_users()
    for chat_id in users:
        try: await bot.send_message(chat_id, "🌙 أذكار المساء | أنس ليلك بذكر ربك.")
        except: pass

# --- معالجات الرسائل ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 تفعيل تنبيهات رمضان", request_location=True)]],
        resize_keyboard=True
    )
    await message.answer(
        "مرحباً بك في بوت (مواقيت رمضان) 🌙\n\n"
        "سيتم تنبيهك بالأذان، الإقامة، الأذكار، وآيات قرآنية كل 3 ساعات.\n"
        "للبدء، شاركنا موقعك:",
        reply_markup=kb
    )

@dp.message(F.location)
async def handle_location(message: types.Message):
    users = load_users()
    users[str(message.chat.id)] = {
        "lat": message.location.latitude,
        "lon": message.location.longitude,
        "l_ad": "", "l_iq": ""
    }
    save_users(users)
    await message.answer("✅ تم التفعيل. مبارك عليك الشهر، جعلنا الله وإياكم من صوامه وقوامه.")

# --- FastAPI & Scheduler ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    if RENDER_URL: await bot.set_webhook(f"{RENDER_URL}/webhook")
    
    # فحص الصلاة كل دقيقة
    scheduler.add_job(check_prayer_notifications, "interval", minutes=1)
    # آية كل 3 ساعات (تبدأ من الساعة 0)
    scheduler.add_job(send_periodic_verse, "interval", hours=3)
    # أذكار الصباح (مثال: 5:30 صباحاً)
    scheduler.add_job(send_adhkar_morning, "cron", hour=5, minute=30)
    # أذكار المساء (مثال: 5:00 مساءً)
    scheduler.add_job(send_adhkar_evening, "cron", hour=17, minute=0)
    
    scheduler.start()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))