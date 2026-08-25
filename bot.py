import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from threading import Thread
from uuid import uuid4

from flask import Flask, jsonify, request

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
    WebAppInfo,
)

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip()
FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL", "").rstrip("/")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "5291965471") or 5291965471)
PORT = int(os.environ.get("PORT", "10000") or 10000)
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
STAR_PRICE = 50
STAR_POINTS = 5_000_000

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not WEB_APP_URL:
    raise RuntimeError("WEB_APP_URL is not set")
if not FIREBASE_DATABASE_URL:
    raise RuntimeError("FIREBASE_DATABASE_URL is not set")

web = Flask(__name__)
application = None
telegram_loop = None


def firebase_url(path: str) -> str:
    encoded = "/".join(
        urllib.parse.quote(part, safe="")
        for part in path.strip("/").split("/")
    )
    return f"{FIREBASE_DATABASE_URL}/{encoded}.json"


def firebase_get(path: str):
    req = urllib.request.Request(firebase_url(path), method="GET")
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def firebase_put(path: str, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        firebase_url(path),
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        response.read()


def telegram_api(method: str, data: dict):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


@web.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@web.get("/")
def root():
    return jsonify({"ok": True, "service": "JuugTAPS"})


@web.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "JuugTAPS"})


@web.get("/bot-info")
def bot_info():
    try:
        result = telegram_api("getMe", {})
        if not result.get("ok"):
            return jsonify({"error": result.get("description", "Telegram error")}), 500
        return jsonify({"username": result["result"].get("username", "")})
    except Exception as exc:
        print("BOT INFO ERROR:", repr(exc), flush=True)
        return jsonify({"error": str(exc)}), 500


@web.get("/create-invoice")
def create_invoice():
    raw_user_id = request.args.get("user_id", "").strip()
    if not raw_user_id.isdigit():
        return jsonify({"error": "invalid user_id"}), 400

    user_id = int(raw_user_id)
    payload = f"stars_5m:{user_id}:{uuid4().hex}"

    try:
        result = telegram_api(
            "createInvoiceLink",
            {
                "title": "5M POINTS",
                "description": "5,000,000 SCORE for 50 Telegram Stars",
                "payload": payload,
                "currency": "XTR",
                "prices": [{"label": "5,000,000 SCORE", "amount": STAR_PRICE}],
            },
        )
        print("CREATE INVOICE RESULT:", result, flush=True)
        if not result.get("ok"):
            return jsonify({"error": result.get("description", "Telegram invoice error")}), 500
        return jsonify({"url": result["result"]})
    except Exception as exc:
        print("CREATE INVOICE ERROR:", repr(exc), flush=True)
        return jsonify({"error": str(exc)}), 500


@web.post("/telegram-webhook")
def telegram_webhook():
    global application, telegram_loop

    if application is None or telegram_loop is None:
        return jsonify({"ok": False, "error": "bot is starting"}), 503

    try:
        data = request.get_json(force=True, silent=False)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.update_queue.put(update),
            telegram_loop,
        )
        return jsonify({"ok": True})
    except Exception as exc:
        print("WEBHOOK UPDATE ERROR:", repr(exc), flush=True)
        return jsonify({"ok": False}), 400


async def send_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎮 PLAY JUUGTAPS", web_app=WebAppInfo(url=WEB_APP_URL))]]
    )
    await update.effective_message.reply_text(
        "🎮 JuugTAPS\n\nНажми кнопку ниже, чтобы открыть игру.",
        reply_markup=keyboard,
    )


async def send_stars_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = f"stars_5m:{user_id}:{uuid4().hex}"
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="5M POINTS",
        description="5,000,000 SCORE for 50 Telegram Stars",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice("5,000,000 SCORE", STAR_PRICE)],
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = context.args[0].strip() if context.args else ""
    if arg == "buy5m":
        await send_stars_invoice(update, context)
        return
    await send_game(update, context)


async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.web_app_data:
        return
    try:
        data = json.loads(message.web_app_data.data)
    except (TypeError, json.JSONDecodeError):
        return
    if data.get("action") == "buy_stars_5m":
        await send_stars_invoice(update, context)


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    valid = (
        query.currency == "XTR"
        and query.total_amount == STAR_PRICE
        and payload.startswith("stars_5m:")
    )
    if not valid:
        await query.answer(ok=False, error_message="Invalid JuugTAPS order")
        return
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    payment = message.successful_payment if message else None
    if not payment:
        return
    if payment.currency != "XTR" or payment.total_amount != STAR_PRICE:
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    charge_id = payment.telegram_payment_charge_id
    payload = payment.invoice_payload or ""

    if not payload.startswith(f"stars_5m:{user_id}:"):
        return

    payment_path = f"payments/{charge_id}"
    if firebase_get(payment_path):
        return

    now = int(time.time() * 1000)
    firebase_put(
        payment_path,
        {
            "userId": user_id,
            "username": username,
            "stars": STAR_PRICE,
            "points": STAR_POINTS,
            "currency": payment.currency,
            "invoicePayload": payload,
            "telegramPaymentChargeId": charge_id,
            "createdAt": now,
        },
    )
    firebase_put(
        f"users/{user_id}/pendingStars/{charge_id}",
        {
            "points": STAR_POINTS,
            "stars": STAR_PRICE,
            "claimed": False,
            "createdAt": now,
        },
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "💰 НОВАЯ ПОКУПКА JUUGTAPS\n\n"
                f"Игрок: @{username}\n"
                f"Telegram ID: {user_id}\n"
                f"Сумма: {STAR_PRICE} ⭐️\n"
                f"Товар: {STAR_POINTS:,} SCORE"
            ).replace(",", " "),
        )
    except Exception as exc:
        print("ADMIN MESSAGE ERROR:", repr(exc), flush=True)

    await message.reply_text(
        "✅ Оплата получена!\n+5 000 000 SCORE будут начислены в игре."
    )


async def paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "По вопросам оплаты JuugTAPS обратитесь к администратору."
    )


def run_flask():
    print(f"JUUGTAPS HTTP STARTING ON 0.0.0.0:{PORT}", flush=True)
    web.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


def run_telegram():
    global application, telegram_loop

    telegram_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telegram_loop)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("paysupport", paysupport))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data)
    )

    async def boot():
        await application.initialize()
        await application.start()

        public_url = RENDER_EXTERNAL_URL
        if not public_url:
            public_url = os.environ.get("RENDER_SERVICE_URL", "").strip().rstrip("/")
        if not public_url:
            public_url = "https://botlol-9x4v.onrender.com"

        webhook_url = f"{public_url}/telegram-webhook"

        result = await application.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=False,
        )
        print("SET WEBHOOK:", webhook_url, result, flush=True)

        info = await application.bot.get_webhook_info()
        print(
            "WEBHOOK INFO:",
            {
                "url": info.url,
                "pending": info.pending_update_count,
                "last_error": info.last_error_message,
            },
            flush=True,
        )

    telegram_loop.run_until_complete(boot())
    print("JUUGTAPS TELEGRAM WEBHOOK READY", flush=True)
    telegram_loop.run_forever()


def main():
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    telegram_thread = Thread(target=run_telegram, daemon=True)
    telegram_thread.start()

    print("JUUGTAPS SERVICE STARTED", flush=True)

    # Keep main process alive.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
