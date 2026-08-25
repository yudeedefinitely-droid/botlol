import json
import os
import time
import urllib.parse
import urllib.request
from threading import Thread
from uuid import uuid4

from flask import Flask, jsonify, request


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip().rstrip("/")
FIREBASE_DATABASE_URL = os.environ.get(
    "FIREBASE_DATABASE_URL", ""
).strip().rstrip("/")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "5291965471") or 5291965471)
PORT = int(os.environ.get("PORT", "10000") or 10000)

STAR_PRICE = 50
STAR_POINTS = 5_000_000
BOT_USERNAME = "juugtapsbot"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not WEB_APP_URL:
    raise RuntimeError("WEB_APP_URL is not set")

if not FIREBASE_DATABASE_URL:
    raise RuntimeError("FIREBASE_DATABASE_URL is not set")

app = Flask(__name__)


def firebase_url(path: str) -> str:
    encoded = "/".join(
        urllib.parse.quote(part, safe="")
        for part in path.strip("/").split("/")
    )
    return f"{FIREBASE_DATABASE_URL}/{encoded}.json"


def firebase_get(path: str):
    req = urllib.request.Request(
        firebase_url(path),
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def firebase_put(path: str, data) -> None:
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
        raw = response.read().decode("utf-8")
        return json.loads(raw)


@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.get("/")
def root():
    return jsonify({"ok": True, "service": "JuugTAPS"})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/bot-info")
def bot_info():
    # Не нужен игре для рефералов, но оставляем endpoint.
    return jsonify({"username": BOT_USERNAME})


@app.get("/create-invoice")
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
                "prices": [
                    {
                        "label": "5,000,000 SCORE",
                        "amount": STAR_PRICE,
                    }
                ],
            },
        )

        print("CREATE INVOICE RESULT:", result, flush=True)

        if not result.get("ok"):
            return jsonify({
                "error": result.get("description", "Telegram invoice error")
            }), 500

        return jsonify({"url": result["result"]})

    except Exception as exc:
        print("CREATE INVOICE ERROR:", repr(exc), flush=True)
        return jsonify({"error": str(exc)}), 500


def game_url(referrer_id: str | None = None) -> str:
    if not referrer_id:
        return WEB_APP_URL

    separator = "&" if "?" in WEB_APP_URL else "?"
    return f"{WEB_APP_URL}{separator}ref={urllib.parse.quote(str(referrer_id))}"


def send_message(chat_id: int, text: str, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return telegram_api("sendMessage", data)


def send_game(chat_id: int, referrer_id: str | None = None):
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "🎮 PLAY JUUGTAPS",
                    "web_app": {
                        "url": game_url(referrer_id)
                    },
                }
            ]
        ]
    }

    return send_message(
        chat_id,
        "🎮 JuugTAPS\n\nНажми кнопку ниже, чтобы открыть игру.",
        reply_markup=markup,
    )


def send_stars_invoice(chat_id: int, user_id: int):
    payload = f"stars_5m:{user_id}:{uuid4().hex}"

    return telegram_api(
        "sendInvoice",
        {
            "chat_id": chat_id,
            "title": "5M POINTS",
            "description": "5,000,000 SCORE for 50 Telegram Stars",
            "payload": payload,
            "currency": "XTR",
            "prices": [
                {
                    "label": "5,000,000 SCORE",
                    "amount": STAR_PRICE,
                }
            ],
        },
    )


def handle_update(update: dict):
    # -------------------------------
    # PRE-CHECKOUT
    # -------------------------------
    pre_checkout = update.get("pre_checkout_query")

    if pre_checkout:
        query_id = pre_checkout.get("id")
        currency = pre_checkout.get("currency")
        amount = pre_checkout.get("total_amount")
        payload = pre_checkout.get("invoice_payload", "")

        valid = (
            currency == "XTR"
            and amount == STAR_PRICE
            and payload.startswith("stars_5m:")
        )

        telegram_api(
            "answerPreCheckoutQuery",
            {
                "pre_checkout_query_id": query_id,
                "ok": valid,
                **({} if valid else {"error_message": "Invalid JuugTAPS order"}),
            },
        )

        return

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user = message.get("from") or {}

    if not chat_id:
        return

    # -------------------------------
    # SUCCESSFUL PAYMENT
    # -------------------------------
    payment = message.get("successful_payment")

    if payment:
        currency = payment.get("currency")
        amount = payment.get("total_amount")
        payload = payment.get("invoice_payload", "")
        charge_id = payment.get("telegram_payment_charge_id", "")
        user_id = int(user.get("id", 0) or 0)
        username = user.get("username") or "без username"

        if (
            currency != "XTR"
            or amount != STAR_PRICE
            or not payload.startswith(f"stars_5m:{user_id}:")
        ):
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
                "currency": currency,
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
            send_message(
                ADMIN_CHAT_ID,
                (
                    "💰 НОВАЯ ПОКУПКА JUUGTAPS\n\n"
                    f"Игрок: @{username}\n"
                    f"Telegram ID: {user_id}\n"
                    f"Сумма: {STAR_PRICE} ⭐️\n"
                    f"Товар: {STAR_POINTS:,} SCORE"
                ).replace(",", " "),
            )
        except Exception as exc:
            print("ADMIN NOTIFICATION ERROR:", repr(exc), flush=True)

        send_message(
            chat_id,
            "✅ Оплата получена!\n"
            "+5 000 000 SCORE будут начислены в игре автоматически.",
        )

        return

    # -------------------------------
    # TEXT / START
    # -------------------------------
    text = (message.get("text") or "").strip()

    if not text.startswith("/start"):
        return

    parts = text.split(maxsplit=1)
    argument = parts[1].strip() if len(parts) > 1 else ""

    if argument == "buy5m":
        send_stars_invoice(
            chat_id,
            int(user.get("id", 0) or 0),
        )
        return

    referrer_id = None

    if argument.startswith("ref_"):
        referrer_id = argument[4:].strip()

    send_game(
        chat_id,
        referrer_id=referrer_id,
    )


@app.post("/telegram-webhook")
def telegram_webhook():
    try:
        update = request.get_json(silent=True) or {}
        handle_update(update)
        return jsonify({"ok": True})
    except Exception as exc:
        print("WEBHOOK ERROR:", repr(exc), flush=True)
        return jsonify({"ok": False, "error": str(exc)}), 200


@app.get("/set-webhook")
def set_webhook_route():
    ok, result = configure_webhook()
    return jsonify({"ok": ok, "telegram": result})


def configure_webhook():
    public_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")

    if not public_url:
        # Fallback to the known Render service URL used by the project.
        public_url = "https://botlol-9x4v.onrender.com"

    webhook_url = f"{public_url}/telegram-webhook"

    try:
        result = telegram_api(
            "setWebhook",
            {
                "url": webhook_url,
                "allowed_updates": [
                    "message",
                    "pre_checkout_query",
                ],
                "drop_pending_updates": False,
            },
        )

        print(
            "SET WEBHOOK:",
            webhook_url,
            result,
            flush=True,
        )

        return bool(result.get("ok")), result

    except Exception as exc:
        print("SET WEBHOOK ERROR:", repr(exc), flush=True)
        return False, {"ok": False, "error": str(exc)}


def run_web():
    app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
    )


if __name__ == "__main__":
    # Webhook не использует getUpdates, поэтому второй экземпляр
    # polling больше не создаёт Conflict.
    configure_webhook()

    print(
        "JUUGTAPS WEBHOOK BOT STARTED",
        flush=True,
    )

    run_web()
