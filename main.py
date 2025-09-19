import os
import calendar
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

TOKEN = os.getenv("TELEGRAM_TOKEN")

# ---------- Pricing grids ----------
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

# ---------- States ----------
CITY, STUDENTS, DATE, LESSONS = range(4)

def round_half_away_from_zero(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("0"), rounding=ROUND_HALF_UP))

def pick_price(city: str, students: int, monthly_forecast: int) -> tuple[int, str]:
    grid = PRICES[city][students]
    if monthly_forecast >= 9:
        return grid["9+"], "9+"
    elif monthly_forecast == 8:
        return grid["8"], "8"
    elif monthly_forecast >= 6:
        return grid["6-7"], "6-7"
    elif monthly_forecast >= 4:
        return grid["4-5"], "4-5"
    else:
        return grid["1-3"], "1-3"

# ---------- Helper ----------
async def send_and_track(chat, text, context, **kwargs):
    """Отправляет сообщение и сохраняет его ID в user_data, чтобы потом удалить"""
    sent = await chat.send_message(text, **kwargs)
    msgs = context.user_data.get("messages", [])
    msgs.append(sent.message_id)
    context.user_data["messages"] = msgs
    return sent

# ---------- Steps ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["Vilnius", "Kaunas", "Klaipėda"]]
    await send_and_track(
        update.message.chat,
        "🇱🇹📍 Choose city:",
        context,
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return CITY

async def choose_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    if city not in PRICES:
        await send_and_track(update.message.chat, "Please tap a city from the keyboard.", context)
        return CITY
    context.user_data["city"] = city

    kb = [["1 student", "2 students"]]
    await send_and_track(
        update.message.chat,
        "👥 How many students attend the lesson?",
        context,
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return STUDENTS

async def choose_students(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").lower()
    students = 2 if "2" in text else 1
    context.user_data["students"] = students

    await send_and_track(
        update.message.chat,
        "📅 Enter the date of the first lesson (DD.MM.YYYY or YYYY-MM-DD):",
        context,
        reply_markup=ReplyKeyboardRemove()
    )
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = (update.message.text or "").strip()
    dt = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if not dt:
        await send_and_track(update.message.chat, "❗ Invalid date. Use DD.MM.YYYY or YYYY-MM-DD.", context)
        return DATE

    context.user_data["first_date"] = dt
    dim = calendar.monthrange(dt.year, dt.month)[1]
    rem = dim - dt.day + 1
    if rem < 1:
        await send_and_track(update.message.chat, "❗ Date seems out of range for the month. Try again.", context)
        return DATE

    context.user_data["days_in_month"] = dim
    context.user_data["days_left"] = rem
    context.user_data["ratio"] = rem / dim

    await send_and_track(
        update.message.chat,
        "🎵 How many lessons does the student want to buy for the rest of this month?",
        context
    )
    return LESSONS

async def compute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await send_and_track(update.message.chat, "❗ Please enter a positive whole number.", context)
        return LESSONS

    lessons_partial = int(text)
    city = context.user_data["city"]
    students = context.user_data["students"]
    first_date: datetime = context.user_data["first_date"]
    dim = context.user_data["days_in_month"]
    rem = context.user_data["days_left"]
    ratio = context.user_data["ratio"]

    monthly_forecast = max(1, round_half_away_from_zero(lessons_partial / ratio))
    price_per_lesson, bucket = pick_price(city, students, monthly_forecast)
    total_price = lessons_partial * price_per_lesson

    context.user_data["details_msg"] = (
        f"📍 City: {city}\n"
        f"👥 Students: {students}\n"
        f"📅 First lesson: {first_date:%d.%m.%Y}\n"
        f"📆 Remaining days: {rem} of {dim} ({ratio:.0%})\n"
        f"🎯 Monthly forecast: {monthly_forecast} lessons → tier {bucket}"
    )

    short_msg = (
        f"🎵 Lessons: {lessons_partial}\n"
        f"💵 Price per lesson: {price_per_lesson} €\n"
        f"💰 Total price: {total_price} €"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Details", callback_data="show_details")],
        [InlineKeyboardButton("🔁 New calculation", callback_data="restart_calc")]
    ])
    sent = await update.message.reply_text(short_msg, reply_markup=keyboard)
    msgs = context.user_data.get("messages", [])
    msgs.append(sent.message_id)
    context.user_data["messages"] = msgs

    return ConversationHandler.END

async def show_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    details = context.user_data.get("details_msg", "No details found.")
    await send_and_track(query.message.chat, details, context)

async def restart_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Удаляем все старые сообщения
    try:
        for msg_id in context.user_data.get("messages", []):
            await query.message.chat.delete_message(msg_id)
    except Exception:
        pass

    context.user_data.clear()

    kb = [["Vilnius", "Kaunas", "Klaipėda"]]
    await send_and_track(
        query.message.chat,
        "🇱🇹📍 Choose city:",
        context,
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return CITY

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_and_track(update.message.chat, "❌ Cancelled.", context, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    if not TOKEN:
        print("❌ Error: please set TELEGRAM_TOKEN environment variable")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_city)],
            STUDENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_students)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            LESSONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, compute)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(show_details, pattern="^show_details$"))
    app.add_handler(CallbackQueryHandler(restart_calc, pattern="^restart_calc$"))

    print("✅ Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
