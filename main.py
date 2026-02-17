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

# إعدادات الإقامة
IQAMAH_OFFSETS = {"Fajr": 25, "Dhuhr": 20, "Asr": 20, "Maghrib": 10, "Isha": 20}

# محتوى الآيات القرآنية حسب الساعة (كل 3 ساعات)
QURAN_VERSES = {
    0: "﴿وَبِالْأَسْحَارِ هُمْ يَسْتَغْفِرُونَ﴾. طوبى للمستغفرين في هذا السحر.",
    3: "﴿وَالصُّبْحِ إِذَا تَنَفَّسَ﴾. اللهم اكتب لنا في هذا الصباح خيراً وبركة.",
    6: "﴿إِنَّ قُرْآنَ الْفَجْرِ كَانَ مَشْهُودًا﴾. ذكر الله في أول النهار فلاح.",
    9: "﴿وَتَزَوَّدُوا فَإِنَّ خَيْرَ الزَّادِ التَّقْوَى﴾. استعن بالله في عملك ويومك.",
    12: "﴿أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ﴾. سبحان الله وبحمده، سبحان الله العظيم.",
    15: "﴿وَاسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ﴾. اقترب موعد الإفطار، صبراً واحتساباً.",
    18: "﴿ثُمَّ أَتِمُّوا الصِّيَامَ إِلَى اللَّيْلِ﴾. هنيئاً لك الإفطار، تقبل الله طاعتك.",
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
        except: return {}
    return {}

def save_users(users):
    try:
        with open(DB_FILE, "w") as f: json.dump(users, f, indent=4)
    except Exception as e: logging.error(f"Error saving DB: {e}")

def add_minutes(time_str, minutes):
    t = datetime.strptime(time_str, "%H:%M")
    return (t + timedelta(minutes=minutes)).strftime("%H:%M")

# --- محرك التنبيهات ---
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
                    is_friday = now_dt.weekday() == 4 # يوم الجمعة

                    updated = False
                    for p_en, p_ar in {"Fajr":"الفجر", "Dhuhr":"الظهر", "Asr":"العصر", "Maghrib":"المغرب", "Isha":"العشاء"}.items():
                        # تعديل صلاة الجمعة
                        if p_en == "Dhuhr" and is_friday: p_ar = "صلاة الجمعة"

                        adhan_time = timings[p_en]
                        iqamah_time = add_minutes(adhan_time, IQAMAH_OFFSETS.get(p_en, 20))

                        # تنبيه الأذان
                        if adhan_time == now_local and info.get("last_adhan") != f"{p_en}_{now_local}":
                            msg = f"🌙 حان الآن موعد أذان {p_ar}\n"
                            if p_en == "Maghrib": msg += "تقبل الله صيامك، هنيئاً لك الإفطار."
                            elif p_en == "Fajr": msg += "صوماً مقبولاً، كفّوا أيديكم وباشروا صلاتكم."
                            else: msg += f"تقام الصلاة بعد {IQAMAH_OFFSETS.get(p_en)} دقيقة."
                            
                            await bot.send_message(chat_id, msg)
                            info["last_adhan"] = f"{p_en}_{now_local}"
                            updated = True

                        # تنبيه الإقامة
                        elif iqamah_time == now_local and info.get("last_iqamah") != f"{p_en}_{now_local}":
                            await bot.send_message(chat_id, f"🕌 إقامة {p_ar}. أقبل على صلاتك بخشوع.")
                            info["last_iqamah"] = f"{p_en}_{now_local}"
                            updated = True

                    if updated:
                        users[chat_id] = info
                        save_users(users)
            except: continue

# --- وظائف التذكير الدوري ---
async def send_periodic_verse():
    users = load_users()
    current_hour = datetime.now().hour
    # اختيار أقرب ساعة مسجلة في القاموس
    closest_hour = (current_hour // 3) * 3
    verse = QURAN_VERSES.get(closest_hour, QURAN_VERSES[12])
    
    for chat_id in users:
        try: await bot.send_message(chat_id, f"📖 {verse}")
        except: pass

async def send_morning_adhkar():
    users = load_users()
    for chat_id in users:
        try: await bot.send_message(chat_id, "☀️ أذكار الصباح | حصن صيامك ويومك بذكر الله.")
        except: pass

async def send_evening_adhkar():
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
        "مبارك عليك شهر رمضان 🌙\n\n"
        "سأقوم بتنبيهك للأذان، الإقامة، الأذكار، وآيات قرآنية كل 3 ساعات.\n"
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
    await message.answer("✅ تم التفعيل. جعلنا الله وإياكم من المقبولين في هذا الشهر الفضيل.")

# --- إعدادات FastAPI والجدولة ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.on_event("startup")
async def on_startup():
    if RENDER_URL: await bot.set_webhook(f"{RENDER_URL}/webhook")
    
    # 1. فحص مواقيت الصلاة كل 30 ثانية
    scheduler.add_job(check_and_send_notifications, "interval", seconds=30)
    
    # 2. إرسال آية كل 3 ساعات
    scheduler.add_job(send_periodic_verse, "interval", hours=3)
    
    # 3. أذكار الصباح (مثلاً الساعة 5:30 فجراً)
    scheduler.add_job(send_morning_adhkar, "cron", hour=5, minute=30)
    
    # 4. أذكار المساء (مثلاً الساعة 5:00 مساءً)
    scheduler.add_job(send_evening_adhkar, "cron", hour=17, minute=0)
    
    scheduler.start()

@app.get("/")
async def index(): return {"status": "Ramadan Bot Active"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000))) 