import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Update
from fastapi import FastAPI, Request
import uvicorn
import aiohttp

# الإعدادات من بيئة التشغيل (Environment Variables)
TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") # سيوفره ريندر تلقائياً

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# --- نفس المنطق السابق للبوت ---
@dp.message(F.text == "/start")
async def start(message: types.Message):
    await message.answer("أهلاً بك في بوت الصلاة على Render! أرسل موقعك الآن.")

@dp.message(F.location)
async def handle_location(message: types.Message):
    lat, lon = message.location.latitude, message.location.longitude
    url = f"http://api.aladhan.com/v1/timings?latitude={lat}&longitude={lon}&method=4"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            t = data['data']['timings']
            res = f"🌅 الفجر: {t['Fajr']}\n☀️ الظهر: {t['Dhuhr']}\n🌇 العصر: {t['Asr']}\n🌆 المغرب: {t['Maghrib']}\n🌃 العشاء: {t['Isha']}"
            await message.answer(res)

# --- إعدادات Webhook الربط مع Render ---
@app.on_event("startup")
async def on_startup():
    webhook_url = f"{RENDER_URL}/webhook"
    await bot.set_webhook(webhook_url)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)

@app.get("/")
async def index():
    return {"status": "bot is running"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
