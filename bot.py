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
    PreCheckoutQueryHandler,
)


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip()
FIREBASE_DATABASE_URL = os.environ.get(
    "FIREBASE_DATABASE_URL",
    ""
).rstrip("/")

ADMIN_CHAT_ID = int(
    os.environ.get(
        "ADMIN_CHAT_ID",
        "0"
    ) or 0
)

PORT = int(
    os.environ.get(
        "PORT",
        "10000"
    )
)

STAR_PRICE = 50
STAR_POINTS = 5_000_000


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not WEB_APP_URL:
    raise RuntimeError("WEB_APP_URL is not set")

if not FIREBASE_DATABASE_URL:
    raise RuntimeError("FIREBASE_DATABASE_URL is not set")


web = Flask(__name__)


def firebase_url(path: str) -> str:
    encoded = "/".join(
        urllib.parse.quote(
            part,
            safe=""
        )
        for part in path.strip("/").split("/")
    )

    return (
        f"{FIREBASE_DATABASE_URL}"
        f"/{encoded}.json"
    )


def firebase_get(path: str):
    request_obj = urllib.request.Request(
        firebase_url(path),
        method="GET"
    )

    with urllib.request.urlopen(
        request_obj,
        timeout=10
    ) as response:

        raw = (
            response
            .read()
            .decode("utf-8")
        )

        return (
            json.loads(raw)
            if raw
            else None
        )


def firebase_put(
    path: str,
    data
) -> None:

    body = json.dumps(
        data
    ).encode("utf-8")

    request_obj = urllib.request.Request(
        firebase_url(path),
        data=body,
        method="PUT",
        headers={
            "Content-Type":
                "application/json"
        }
    )

    with urllib.request.urlopen(
        request_obj,
        timeout=10
    ) as response:

        response.read()


def telegram_api(
    method: str,
    data: dict
):

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    body = json.dumps(
        data
    ).encode("utf-8")

    request_obj = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type":
                "application/json"
        }
    )

    with urllib.request.urlopen(
        request_obj,
        timeout=15
    ) as response:

        return json.loads(
            response
            .read()
            .decode("utf-8")
        )


@web.after_request
def add_cors(response):

    response.headers[
        "Access-Control-Allow-Origin"
    ] = "*"

    response.headers[
        "Access-Control-Allow-Methods"
    ] = "GET, OPTIONS"

    response.headers[
        "Access-Control-Allow-Headers"
    ] = "Content-Type"

    return response


@web.get("/healthz")
def healthz():

    return jsonify({
        "ok": True,
        "service": "JuugTAPS"
    })


@web.get("/bot-info")
def bot_info():

    try:

        result =
            telegram_api(
                "getMe",
                {}
            )

        if not result.get("ok"):

            return jsonify({
                "error":
                    result.get(
                        "description",
                        "Telegram error"
                    )
            }), 500

        return jsonify({
            "username":
                result["result"].get(
                    "username",
                    ""
                )
        })

    except Exception as exc:

        print(
            "BOT INFO ERROR:",
            repr(exc),
            flush=True
        )

        return jsonify({
            "error":
                "bot info unavailable"
        }), 500


@web.get("/create-invoice")
def create_invoice():

    raw_user_id = (
        request
        .args
        .get(
            "user_id",
            ""
        )
        .strip()
    )

    if not raw_user_id.isdigit():

        return jsonify({
            "error":
                "invalid user_id"
        }), 400

    user_id = int(
        raw_user_id
    )

    payload = (
        f"stars_5m:"
        f"{user_id}:"
        f"{uuid4().hex}"
    )

    try:

        result = telegram_api(
            "createInvoiceLink",
            {
                "title":
                    "5M POINTS",

                "description":
                    "Instant +5,000,000 JuugTAPS points",

                "payload":
                    payload,

                "currency":
                    "XTR",

                "prices": [
                    {
                        "label":
                            "5,000,000 points",

                        "amount":
                            STAR_PRICE
                    }
                ]
            }
        )

        if not result.get("ok"):

            return jsonify({
                "error":
                    result.get(
                        "description",
                        "Telegram error"
                    )
            }), 500

        return jsonify({
            "url":
                result["result"]
        })

    except Exception as exc:

        print(
            "INVOICE ERROR:",
            repr(exc),
            flush=True
        )

        return jsonify({
            "error":
                "could not create invoice"
        }), 500


def run_web():

    web.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False
    )


async def send_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard =
        InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎮 PLAY JUUGTAPS",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ]
        ])

    await update.effective_message.reply_text(
        "🎮 JuugTAPS\n\n"
        "Нажми кнопку ниже, чтобы открыть игру.",
        reply_markup=keyboard
    )


async def send_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id =
        update.effective_user.id

    payload = (
        f"stars_5m:"
        f"{user_id}:"
        f"{uuid4().hex}"
    )

    await context.bot.send_invoice(
        chat_id=
            update.effective_chat.id,

        title=
            "5M POINTS",

        description=
            "Instant +5,000,000 JuugTAPS points",

        payload=
            payload,

        currency=
            "XTR",

        prices=[
            LabeledPrice(
                "5,000,000 points",
                STAR_PRICE
            )
        ]
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    argument = (
        context.args[0].strip()
        if context.args
        else ""
    )

    if argument == "buy5m":

        await send_invoice(
            update,
            context
        )

        return

    await send_game(
        update,
        context
    )


async def pre_checkout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query =
        update.pre_checkout_query

    payload = (
        query.invoice_payload
        or ""
    )

    valid = (
        query.currency == "XTR"
        and
        query.total_amount == STAR_PRICE
        and
        payload.startswith(
            "stars_5m:"
        )
    )

    if not valid:

        await query.answer(
            ok=False,
            error_message=
                "Invalid JuugTAPS order"
        )

        return

    await query.answer(
        ok=True
    )


async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message =
        update.effective_message

    payment = (
        message.successful_payment
        if message
        else None
    )

    if not payment:
        return

    if (
        payment.currency != "XTR"
        or
        payment.total_amount != STAR_PRICE
    ):
        return

    user_id =
        update.effective_user.id

    charge_id =
        payment.telegram_payment_charge_id

    payload =
        payment.invoice_payload or ""

    if not payload.startswith(
        f"stars_5m:{user_id}:"
    ):
        return

    payment_path =
        f"payments/{charge_id}"

    if firebase_get(
        payment_path
    ):

        await message.reply_text(
            "✅ Оплата уже обработана."
        )

        return

    now_ms =
        int(
            time.time() *
            1000
        )

    firebase_put(
        payment_path,
        {
            "userId":
                user_id,

            "stars":
                STAR_PRICE,

            "points":
                STAR_POINTS,

            "currency":
                payment.currency,

            "invoicePayload":
                payload,

            "telegramPaymentChargeId":
                charge_id,

            "createdAt":
                now_ms
        }
    )

    firebase_put(
        f"users/{user_id}/pendingStars/{charge_id}",
        {
            "points":
                STAR_POINTS,

            "stars":
                STAR_PRICE,

            "claimed":
                False,

            "createdAt":
                now_ms
        }
    )

    if ADMIN_CHAT_ID:

        username = (
            update
            .effective_user
            .username
            or
            "без username"
        )

        try:

            await context.bot.send_message(
                chat_id=
                    ADMIN_CHAT_ID,

                text=(
                    "💰 Новая покупка JuugTAPS\n\n"
                    f"Игрок: @{username}\n"
                    f"Telegram ID: {user_id}\n"
                    f"Сумма: {STAR_PRICE} ⭐️\n"
                    f"Товар: {STAR_POINTS:,} очков"
                ).replace(
                    ",",
                    " "
                )
            )

        except Exception as exc:

            print(
                "ADMIN NOTIFY ERROR:",
                repr(exc),
                flush=True
            )

    await message.reply_text(
        "✅ Оплата получена!\n"
        "+5 000 000 очков будут начислены "
        "в игре автоматически."
    )


async def paysupport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.effective_message.reply_text(
        "По вопросам покупки JuugTAPS "
        "обратитесь к администратору игры."
    )


def main():

    Thread(
        target=run_web,
        daemon=True
    ).start()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "paysupport",
            paysupport
        )
    )

    application.add_handler(
        PreCheckoutQueryHandler(
            pre_checkout
        )
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment
        )
    )

    print(
        "JuugTAPS bot is running",
        flush=True
    )

    application.run_polling(
        allowed_updates=
            Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
