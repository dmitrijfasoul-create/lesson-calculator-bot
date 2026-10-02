import os
import calendar
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from analytics import (
    AnalyticsStore,
    clamp_to_current_month,
    format_stats_report,
    get_current_local_month,
    next_month as get_next_month,
    previous_month,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ANALYTICS = AnalyticsStore()

# ---------- Pricing ----------
with open("prices.json", "r", encoding="utf-8") as f:
    PRICES = json.load(f)

# ---------- Helpers ----------
def round_half_away(x):
    return int(Decimal(str(x)).quantize(Decimal("0"), rounding=ROUND_HALF_UP))

def pick_price(city, students, forecast):
    grid = PRICES[city][str(students)]
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

async def begin_calculation(chat, context, user_id=None, incoming_message=None):
    await clear_chat(chat, context)
    context.user_data["analytics_session_id"] = ANALYTICS.start_session(user_id)
    kb = [["Vilnius", "Kaunas", "Klaipėda"]]
    m = await chat.send_message(
        "🇱🇹📍 Choose city:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    context.user_data["step"] = "city"
    await track_message(incoming_message, context)
    await track_message(m, context)

def admin_stats_keyboard(year, month):
    current_year, current_month = get_current_local_month()
    buttons = [
        [InlineKeyboardButton("◀ Previous month", callback_data=f"admin_stats:{previous_month(year, month)[0]}:{previous_month(year, month)[1]}")],
        [InlineKeyboardButton("Current month", callback_data=f"admin_stats:{current_year}:{current_month}")],
    ]
    if (year, month) < (current_year, current_month):
        next_year, next_month_value = get_next_month(year, month)
        buttons.append([InlineKeyboardButton("Next month ▶", callback_data=f"admin_stats:{next_year}:{next_month_value}")])
    buttons.append([InlineKeyboardButton("🔄 New calculation", callback_data="restart_calc")])
    return InlineKeyboardMarkup(buttons)

async def send_admin_stats(target, user_id, year=None, month=None):
    if not ANALYTICS.is_admin(user_id):
        await target.reply_text("Access denied.")
        return

    if year is None or month is None:
        year, month = get_current_local_month()
    year, month = clamp_to_current_month(year, month)
    stats = ANALYTICS.stats_for_month(year, month)
    await target.reply_text(
        format_stats_report(stats),
        reply_markup=admin_stats_keyboard(year, month)
    )

# ---------- Steps ----------
async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧮 Start calculation", callback_data="start_calc")]
    ])
    await update.message.reply_text(f"Your Telegram ID: {user.id}", reply_markup=kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    await begin_calculation(update.message.chat, context, user_id, update.message)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    await send_admin_stats(update.message, user_id)

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
        ANALYTICS.set_city(context.user_data.get("analytics_session_id"), text)
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
            "📅 Enter the date of the first paid lesson (DD.MM.YYYY or YYYY-MM-DD):\n(for example: 20.09.2025)",
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
            f"📅 First paid lesson: {first_date:%d.%m.%Y}\n"
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
        ANALYTICS.complete_session(context.user_data.get("analytics_session_id"))

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
    user_id = q.from_user.id if q.from_user else None
    await begin_calculation(q.message.chat, context, user_id)

async def start_calc_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id if q.from_user else None
    await begin_calculation(q.message.chat, context, user_id)

async def admin_stats_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id if q.from_user else None
    parts = q.data.split(":")
    year = int(parts[1])
    month = int(parts[2])
    if not ANALYTICS.is_admin(user_id):
        await q.message.reply_text("Access denied.")
        return
    year, month = clamp_to_current_month(year, month)
    stats = ANALYTICS.stats_for_month(year, month)
    await q.message.edit_text(
        format_stats_report(stats),
        reply_markup=admin_stats_keyboard(year, month)
    )

# ---------- Run ----------
def main():
    if not TOKEN:
        print("❌ TELEGRAM_TOKEN not set")
        return

    ANALYTICS.initialize()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("my_id", my_id))
    app.add_handler(CommandHandler("admin_stats", admin_stats))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(admin_stats_button, pattern="^admin_stats:"))
    app.add_handler(CallbackQueryHandler(start_calc_button, pattern="^start_calc$"))
    app.add_handler(CallbackQueryHandler(show_details, pattern="^show_details$"))
    app.add_handler(CallbackQueryHandler(restart_calc, pattern="^restart_calc$"))
    print("✅ Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
