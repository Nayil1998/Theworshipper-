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

IQAMAH_OFFSETS = {"Fajr": 25, "Dhuhr": 20, "Asr": 20, "Maghrib": 10, "Isha": 20}

# آيات قرآنية مختارة (تتماشى مع الوقت)
QURAN_MESSAGES = {
    0: "﴿وَبِالْأَسْحَارِ هُمْ يَسْتَغْفِرُونَ﴾.. لا تنسَ السحور والاستغفار.",
    3: "﴿وَالصُّبْحِ إِذَا تَنَفَّسَ﴾.. صباحك طاعة وبركة.",
    6: "﴿إِنَّ قُرْآنَ الْفَجْرِ كَانَ مَشْهُودًا﴾.. ذكر الله أول النهار فلاح.",
    9: "﴿وَتَزَوَّدُوا فَإِنَّ خَيْرَ الزَّادِ التَّقْوَى﴾.. ضحى مبارك.",
    12: "﴿أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ﴾.. ذكر الله راحة للروح.",
    15: "﴿وَاسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ﴾.. ساعات ويحين الإفطار، صبراً جميلاً.",
    18: "﴿ثُمَّ أَتِمُّوا الصِّيَامَ إِلَى اللَّيْلِ﴾.. ذهب الظمأ وابتلت العروق، تقبل الله منك.",
    21: "﴿إِنَّا أَنْزَلْنَاهُ فِي لَيْلَةِ الْقَدْرِ﴾.. طابت ليلتك بالقيام والقرآن."
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
        except: return {}
    return {}

def save_users(users):
    with open(DB_FILE, "w") as f: json.dump(users, f, indent=4)

def add_minutes(time_str, minutes):
    t = datetime.strptime(time_str, "%H:%M")
    return (t + timedelta(minutes=minutes)).strftime("%H:%M")

def get_prayer_name(p_en, is_friday):
    if p_en == "Dhuhr" and is_friday: return "صلاة الجمعة"
    names = {"Fajr": "الفجر", "Dhuhr": "الظهر", "Asr": "العصر", "Maghrib": "المغرب", "Isha": "العشاء"}
    return names.get(p_en)

# --- المهام المجدولة ---

async def check_prayer_and_notify():
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

                        if adhan_t == now_str and info.get("l_ad") != f"{p_en}_{now_str}":
                            msg = f"🌙 حان أذان {p_ar}\n"
                            if p_en == "Maghrib": msg += "تقبل الله صيامكم، إفطاراً شهياً."
                            elif p_en == "Fajr": msg += "بادر بالصلاة، صوماً مقبولاً."
                            await bot.send_message(chat_id, msg)
                            info["l_ad"] = f"{p_en}_{now_str}"
                            save_users(users)

                        elif iqamah_t == now_str and info.get("l_iq") != f"{p_en}_{now_str}":
                            await bot.send_message(chat_id, f"🕌 إقامة {p_ar}.. صلاة بخشوع ترتقي بالروح.")
                            info["l_iq"] = f"{p_en}_{now_str}"
                            save_users(users)
            except: continue

async def send_quran_verse():
    users = load_users()
    hour = datetime.now().hour
    closest_hour = (hour // 3) * 3
    verse = QURAN_MESSAGES.get(closest_hour, QURAN_MESSAGES[12])
    for chat_id in users:
        try: await bot.send_message(chat_id, f"📖 {verse}")
        except: pass

async def send_daily_adhkar(type="morning"):
    users = load_users()
    msg = "☀️ أذكار الصباح | زادُك في صيامك." if type == "morning" else "🌙 أذكار المساء | حِصنك من كل سوء."
    for chat_id in users:
        try: await bot.send_message(chat_id, msg)
        except: pass

# --- المعالجات ---
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📍 تفعيل تنبيهات رمضان", request_location=True)]], resize_keyboard=True)
    await message.answer("مبارك عليك الشهر 🌙\n\nسأقوم بتنبيهك للأذان والإقامة، وتذكيرك بالأذكار وآيات قرآنية كل 3 ساعات.\n\nمن فضلك شارك موقعك للبدء:", reply_markup=kb)

@dp.message(F.location)
async def handle_location(message: types.Message):
    users = load_users()
    users[str(message.chat.id)] = {"lat": message.location.latitude, "lon": message.location.longitude, "l_ad": "", "l_iq": ""}
    save_users(users)
    await message.answer("✅ تم التفعيل بنجاح. جعلنا الله وإياكم من المقبولين.")

# --- Webhook & FastAPI ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    if RENDER_URL: await bot.set_webhook(f"{RENDER_URL}/webhook")
    scheduler.add_job(check_prayer_and_notify, "interval", minutes=1)
    scheduler.add_job(send_quran_verse, "interval", hours=3)
    scheduler.add_job(send_daily_adhkar, "cron", hour=5, minute=30, args=["morning"])
    scheduler.add_job(send_daily_adhkar, "cron", hour=17, minute=0, args=["evening"])
    scheduler.start()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))