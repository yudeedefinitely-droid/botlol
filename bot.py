import json
import os
import time
import urllib.parse
import urllib.request
from uuid import uuid4

from flask import Flask, jsonify, request


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip().rstrip("/")
FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL", "").strip().rstrip("/")
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

web = Flask(__name__)


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


def firebase_put(path: str, data) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
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
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
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
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@web.get("/")
def root():
    return jsonify({"ok": True, "service": "JuugTAPS"})


@web.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "JuugTAPS"})


@web.get("/debug-config")
def debug_config():
    """Does not expose secrets; only confirms which config is present."""
    return jsonify({
        "bot_token": bool(BOT_TOKEN),
        "web_app_url": WEB_APP_URL,
        "firebase_url": bool(FIREBASE_DATABASE_URL),
        "admin_chat_id": ADMIN_CHAT_ID,
        "port": PORT,
    })


@web.get("/bot-info")
def bot_info():
    return jsonify({"username": BOT_USERNAME})


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
                "prices": [
                    {"label": "5,000,000 SCORE", "amount": STAR_PRICE}
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


def build_webapp_url(user_id: int, referral: str = "") -> str:
    params = {"tg_user_id": str(user_id)}
    if referral:
        params["ref"] = referral
    return f"{WEB_APP_URL}?{urllib.parse.urlencode(params)}"


def send_message(chat_id: int, text: str, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    return telegram_api("sendMessage", data)


def answer_pre_checkout(query_id: str, ok: bool, error_message: str | None = None):
    data = {"pre_checkout_query_id": query_id, "ok": ok}
    if not ok and error_message:
        data["error_message"] = error_message
    return telegram_api("answerPreCheckoutQuery", data)


def send_game(chat_id: int, user_id: int, referral: str = ""):
    url = build_webapp_url(user_id, referral)
    keyboard = {
        "inline_keyboard": [[
            {
                "text": "🎮 PLAY JUUGTAPS",
                "web_app": {"url": url},
            }
        ]]
    }
    return send_message(
        chat_id,
        "🎮 JuugTAPS\n\nНажми кнопку ниже, чтобы открыть игру.",
        keyboard,
    )


def send_invoice_to_chat(chat_id: int, user_id: int):
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
                {"label": "5,000,000 SCORE", "amount": STAR_PRICE}
            ],
        },
    )


def process_update(update: dict):
    # /start, /start ref_..., /start buy5m
    message = update.get("message") or {}
    if message:
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = user.get("id")
        text = message.get("text") or ""

        if chat_id and user_id and text.startswith("/start"):
            parts = text.split(maxsplit=1)
            argument = parts[1].strip() if len(parts) > 1 else ""

            if argument == "buy5m":
                result = send_invoice_to_chat(chat_id, user_id)
                print("SEND INVOICE RESULT:", result, flush=True)
                return

            referral = argument[4:] if argument.startswith("ref_") else ""
            result = send_game(chat_id, user_id, referral)
            print("SEND GAME RESULT:", result, flush=True)
            return

        if chat_id and user_id and text.startswith("/paysupport"):
            send_message(
                chat_id,
                "По вопросам оплаты JuugTAPS обратитесь к администратору.",
            )
            return

        payment = message.get("successful_payment")
        if payment:
            handle_successful_payment(update, payment)
            return

    pre_checkout = update.get("pre_checkout_query")
    if pre_checkout:
        handle_pre_checkout(pre_checkout)
        return


def handle_pre_checkout(query: dict):
    payload = query.get("invoice_payload") or ""
    currency = query.get("currency")
    amount = int(query.get("total_amount") or 0)
    query_id = query.get("id")

    valid = (
        currency == "XTR"
        and amount == STAR_PRICE
        and payload.startswith("stars_5m:")
    )

    print("PRE CHECKOUT:", {
        "valid": valid,
        "currency": currency,
        "amount": amount,
        "payload": payload,
    }, flush=True)

    if query_id:
        answer_pre_checkout(
            query_id,
            valid,
            None if valid else "Invalid JuugTAPS order",
        )


def handle_successful_payment(update: dict, payment: dict):
    message = update.get("message") or {}
    user = message.get("from") or {}
    user_id = int(user.get("id") or 0)
    username = user.get("username") or "без username"

    currency = payment.get("currency")
    amount = int(payment.get("total_amount") or 0)
    charge_id = payment.get("telegram_payment_charge_id") or ""
    payload = payment.get("invoice_payload") or ""

    if not user_id or not charge_id:
        return

    if currency != "XTR" or amount != STAR_PRICE:
        print("INVALID PAYMENT:", payment, flush=True)
        return

    if not payload.startswith(f"stars_5m:{user_id}:"):
        print("INVALID PAYMENT PAYLOAD:", payload, flush=True)
        return

    payment_path = f"payments/{charge_id}"
    if firebase_get(payment_path):
        print("PAYMENT ALREADY PROCESSED:", charge_id, flush=True)
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
        print("ADMIN MESSAGE ERROR:", repr(exc), flush=True)

    try:
        send_message(
            user_id,
            "✅ Оплата получена!\n5 000 000 SCORE будут начислены в игре автоматически.",
        )
    except Exception as exc:
        print("BUYER MESSAGE ERROR:", repr(exc), flush=True)


@web.post("/telegram-webhook")
def telegram_webhook():
    try:
        update = request.get_json(silent=True) or {}
        print(
            "TELEGRAM UPDATE:",
            json.dumps(update, ensure_ascii=False)[:4000],
            flush=True,
        )
        process_update(update)
        return "OK", 200
    except Exception as exc:
        print("WEBHOOK ERROR:", repr(exc), flush=True)
        # Telegram should not be retried forever for application errors.
        return "OK", 200


def configure_webhook():
    external_url = (
        os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    )
    if not external_url:
        hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
        if hostname:
            external_url = f"https://{hostname}"

    if not external_url:
        print("ERROR: RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME is missing", flush=True)
        return False

    webhook_url = f"{external_url}/telegram-webhook"

    try:
        result = telegram_api(
            "deleteWebhook",
            {"drop_pending_updates": False},
        )
        print("DELETE WEBHOOK:", result, flush=True)

        result = telegram_api(
            "setWebhook",
            {
                "url": webhook_url,
                "allowed_updates": ["message", "pre_checkout_query"],
            },
        )
        print("SET WEBHOOK:", result, flush=True)

        info = telegram_api("getWebhookInfo", {})
        print("WEBHOOK INFO:", info, flush=True)
        return bool(result.get("ok"))

    except Exception as exc:
        print("WEBHOOK SETUP ERROR:", repr(exc), flush=True)
        return False


if __name__ == "__main__":
    print("JUUGTAPS BOT WEBHOOK STARTING", flush=True)
    configure_webhook()
    print(f"LISTENING ON 0.0.0.0:{PORT}", flush=True)
    web.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )
