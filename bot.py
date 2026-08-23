import json
import os
import urllib.parse
import urllib.request
from uuid import uuid4

from telegram import Update, LabeledPrice, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters

# ================================================================
# JUUGTAPS BOT: Telegram Stars payments
# ================================================================
# Environment variables:
#   BOT_TOKEN=123456:ABC...
#   WEB_APP_URL=https://YOUR_GITHUB_PAGES_OR_SITE/index.html
#   FIREBASE_DATABASE_URL=https://YOUR_PROJECT-default-rtdb.europe-west1.firebasedatabase.app
#
# Install:
#   pip install -r requirements.txt
# Run:
#   python bot.py
# ================================================================


# Render compatibility: keep a tiny HTTP endpoint alive on the PORT Render provides.
# This lets the same process run the Telegram polling bot and satisfy Web Service health checks.
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

PORT = int(os.environ.get("PORT", "10000"))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"JuugTAPS bot is running")

    def log_message(self, format, *args):
        return

def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip()
FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL", "").rstrip("/")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "5291965471").strip())

STAR_PRICE = 50
STAR_POINTS = 5_000_000

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not FIREBASE_DATABASE_URL:
    raise RuntimeError("FIREBASE_DATABASE_URL is not set")


def firebase_url(path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in path.strip("/").split("/"))
    return f"{FIREBASE_DATABASE_URL}/{encoded}.json"


def firebase_get(path: str):
    req = urllib.request.Request(firebase_url(path), method="GET")
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def firebase_put(path: str, data) -> None:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        firebase_url(path), data=body, method="PUT", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        response.read()


def firebase_patch(path: str, data: dict) -> None:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        firebase_url(path), data=body, method="PATCH", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        response.read()


def parse_start_arg(update: Update) -> str:
    text = update.effective_message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


async def send_game_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if WEB_APP_URL:
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("🎮 PLAY JUUGTAPS", web_app=WebAppInfo(url=WEB_APP_URL))]],
            resize_keyboard=True,
            is_persistent=True,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎮 JuugTAPS\n\nОткрой игру кнопкой ниже.",
            reply_markup=keyboard,
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Игра пока недоступна. Обратись к администратору.",
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # context.args надёжно работает для /start buy5m и Telegram deep link.
    arg = " ".join(context.args).strip() if context.args else parse_start_arg(update)

    if arg == "buy5m":
        await send_stars_invoice(update, context)
        return

    await send_game_keyboard(update, context)


async def send_stars_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    payload = f"stars_5m:{user_id}:{uuid4().hex}"

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="5 000 000 очков",
        description="50 ⭐️ = 5 000 000 очков JuugTAPS",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice("5,000,000 points", STAR_PRICE)],
    )


async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Supports the old game's sendData() flow too."""
    message = update.effective_message
    if not message or not message.web_app_data:
        return

    try:
        data = json.loads(message.web_app_data.data)
    except (TypeError, json.JSONDecodeError):
        return

    action = data.get("action")
    if action == "buy_stars_5m":
        await send_stars_invoice(update, context)


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""

    if query.currency != "XTR" or query.total_amount != STAR_PRICE or not payload.startswith("stars_5m:"):
        await query.answer(ok=False, error_message="Invalid order")
        return

    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    payment = message.successful_payment
    if not payment:
        return

    if payment.currency != "XTR" or payment.total_amount != STAR_PRICE:
        return

    user_id = update.effective_user.id
    charge_id = payment.telegram_payment_charge_id
    payload = payment.invoice_payload or ""

    if not payload.startswith(f"stars_5m:{user_id}:"):
        return

    # Idempotency: Telegram payment charge IDs are unique.
    payment_path = f"payments/{charge_id}"
    existing = firebase_get(payment_path)
    if existing:
        await message.reply_text("✅ This payment was already processed.")
        return

    # Store the payment record first.
    firebase_put(
        payment_path,
        {
            "userId": user_id,
            "stars": STAR_PRICE,
            "points": STAR_POINTS,
            "currency": payment.currency,
            "invoicePayload": payload,
            "telegramPaymentChargeId": charge_id,
            "createdAt": __import__("time").time_ns() // 1_000_000,
        },
    )

    # The game consumes this pending purchase and adds the points to the player's score.
    firebase_put(
        f"users/{user_id}/pendingStars/{charge_id}",
        {
            "points": STAR_POINTS,
            "stars": STAR_PRICE,
            "claimed": False,
            "createdAt": __import__("time").time_ns() // 1_000_000,
        },
    )

    username = update.effective_user.username
    display_name = f"@{username}" if username else str(user_id)

    # Уведомление владельцу/администратору.
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "💰 Новая покупка\n\n"
                f"Игрок: {display_name}\n"
                f"Telegram ID: {user_id}\n"
                f"Сумма: {STAR_PRICE} ⭐️\n"
                f"Товар: {STAR_POINTS:,} очков".replace(',', ' ')
            )
        )
    except Exception as admin_error:
        print("ADMIN NOTIFICATION ERROR:", admin_error)

    await message.reply_text(
        "✅ Оплата получена!\n"
        "5 000 000 очков уже отправлены в игру."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("BOT ERROR:", context.error)


def main() -> None:
    Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))
    app.add_error_handler(error_handler)

    print("JuugTAPS bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
