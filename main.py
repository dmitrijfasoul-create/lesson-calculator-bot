import os
import calendar
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.getenv("TELEGRAM_TOKEN")

# ---------- Pricing ----------
PRICES = {
    "Vilnius": {
        1: {"1-3": 50, "4-5": 40, "6-7": 35, "8": 33, "9+": 30},
        2: {"1-3": 65, "4-5": 55, "6-7": 50, "8": 45, "9+": 40},
    },
    "Kaunas": {
        1: {"1-3": 40, "4-5": 35, "6-7": 32, "8": 30, "9+": 28},
        2: {"1-3": 55, "4-5": 50, "6-7": 45, "8": 40, "9+": 35},
    },
    "Klaipėda": {
        1: {"1-3": 35, "4-5": 30, "6-7": 27, "8": 25, "9+": 23},
        2: {"1-3": 50, "4-5": 45, "6-7": 40, "8": 35, "9+": 30},
    },
}

# ---------- Helpers ----------
def round_half_away(x):
    return int(Decimal(str(x)).quantize(Decimal("0"), rounding=ROUND_HALF_UP))

def pick_price(city, students, forecast):
    grid = PRICES[city][students]
    if forecast >= 9:
        return grid["9+"], "9+"
    elif forecast == 8:
        return grid["8"], "8"
    elif forecast >= 6:
        return grid["6-7"], "6-7"
    elif forecast >= 4:
        return grid["4-5"], "4-5"
    else:
        return grid["1-3"], "1-3"

async def track_message(msg, context):
    """Сохраняем все id сообщений (бота и пользователя)"""
    if not msg:
        return
    ids = context.user_data.get("msgs", [])
    ids.append(msg.message_id)
    context.user_data["msgs"] = ids

async def clear_chat(chat, context):
    """Удаляем все сообщения из предыдущего расчёта"""
    for mid in context.user_data.get("msgs", []):
        try:
            await chat.delete_message(mid)
        except Exception:
            pass
    context.user_data.clear()

# ---------- Steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clear_chat(update.message.chat, context)
    kb = [["Vilnius", "Kaunas", "Klaipėda"]]
    m = await update.message.reply_text(
        "🇱🇹📍 Choose city:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    context.user_data["step"] = "city"
    await track_message(update.message, context)
    await track_message(m, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    step = context.user_data.get("step")
    await track_message(update.message, context)

    if step == "city":
        if text not in PRICES:
            m = await update.message.reply_text("Please choose from the keyboard.")
            await track_message(m, context)
            return
        context.user_data["city"] = text
        kb = [["1 student", "2 students"]]
        m = await update.message.reply_text(
            "👥 How many students attend the lesson?",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        context.user_data["step"] = "students"
        await track_message(m, context)

    elif step == "students":
        students = 2 if "2" in text else 1
        context.user_data["students"] = students
        m = await update.message.reply_text(
            "📅 Enter the date of the first lesson (DD.MM.YYYY or YYYY-MM-DD):",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["step"] = "date"
        await track_message(m, context)

    elif step == "date":
        dt = None
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if not dt:
            m = await update.message.reply_text("❗ Invalid date. Try again.")
            await track_message(m, context)
            return
        context.user_data["first_date"] = dt
        dim = calendar.monthrange(dt.year, dt.month)[1]
        rem = dim - dt.day + 1
        if rem < 1:
            m = await update.message.reply_text("❗ Date out of range.")
            await track_message(m, context)
            return
        context.user_data["days_in_month"] = dim
        context.user_data["days_left"] = rem
        context.user_data["ratio"] = rem / dim
        m = await update.message.reply_text("🎵 How many lessons does the student want to buy?")
        context.user_data["step"] = "lessons"
        await track_message(m, context)

    elif step == "lessons":
        if not text.isdigit() or int(text) <= 0:
            m = await update.message.reply_text("❗ Enter a positive number.")
            await track_message(m, context)
            return
        lessons = int(text)
        city = context.user_data["city"]
        students = context.user_data["students"]
        first_date = context.user_data["first_date"]
        dim = context.user_data["days_in_month"]
        rem = context.user_data["days_left"]
        ratio = context.user_data["ratio"]

        forecast = max(1, round_half_away(lessons / ratio))
        price, tier = pick_price(city, students, forecast)
        total = lessons * price

        context.user_data["details"] = (
            f"📍 City: {city}\n"
            f"👥 Students: {students}\n"
            f"📅 First lesson: {first_date:%d.%m.%Y}\n"
            f"📆 Remaining days: {rem} of {dim} ({ratio:.0%})\n"
            f"🎯 Forecast: {forecast} lessons → tier {tier}"
        )

        msg = (
            f"🎵 Lessons: {lessons}\n"
            f"💵 Price per lesson: {price} €\n"
            f"💰 Total price: {total} €"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Details", callback_data="show_details")],
            [InlineKeyboardButton("🔁 New calculation", callback_data="restart_calc")]
        ])
        m = await update.message.reply_text(msg, reply_markup=kb)
        await track_message(m, context)

        context.user_data["step"] = "done"

async def show_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = context.user_data.get("details", "No details.")
    m = await q.message.reply_text(d)
    await track_message(m, context)

async def restart_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await clear_chat(q.message.chat, context)

    kb = [["Vilnius", "Kaunas", "Klaipėda"]]
    m = await q.message.chat.send_message(
        "🇱🇹📍 Choose city:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    context.user_data["step"] = "city"
    await track_message(m, context)

# ---------- Run ----------
def main():
    if not TOKEN:
        print("❌ TELEGRAM_TOKEN not set")
        return

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(show_details, pattern="^show_details$"))
    app.add_handler(CallbackQueryHandler(restart_calc, pattern="^restart_calc$"))
    print("✅ Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
