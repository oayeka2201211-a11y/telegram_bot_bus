# main_bot.py
import os
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from utils import database as db
from utils.logger import logger
from utils.menu import get_bot_commands, get_reply_keyboard
from bots.buyer_bot import (
    get_buyer_conversation, debug_state, view_cart, pay_for_products,
    start_buyer_flow, start_place_order, debug_products
)

load_dotenv()
TOKEN = os.getenv("MAIN_BOT_TOKEN")
if not TOKEN or not TOKEN.strip():
    raise RuntimeError("MAIN_BOT_TOKEN is required but not set.")

# --------------------------
# Menu Command Handlers
# --------------------------
async def cmd_view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await view_cart(update, context)

def _format_amount(amount):
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return f"₦{amount}"

    if value.is_integer():
        return f"₦{int(value):,}"
    return f"₦{value:,.2f}"


async def btn_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id

    try:
        all_orders = db.orders_collection.find({"telegram_id": telegram_id})
    except Exception:
        logger.exception("Failed to fetch orders for My Orders")
        await update.message.reply_text(
            "❌ Could not load your orders right now. Please try again later.",
            reply_markup=get_reply_keyboard(),
        )
        return

    pending_orders = []
    for order in all_orders:
        status = str(order.get("status", "")).strip().lower()
        if status in {"delivered", "cancelled"}:
            continue
        pending_orders.append(order)

    pending_orders.sort(key=lambda item: str(item.get("date_time", item.get("created_at", ""))), reverse=True)

    if not pending_orders:
        await update.message.reply_text(
            "📦 You have no current pending deliveries.",
            reply_markup=get_reply_keyboard(),
        )
        return

    lines = ["📦 Your current orders:"]
    for index, order in enumerate(pending_orders, start=1):
        order_id = order.get("order_id") or order.get("_id") or "N/A"
        product_name = order.get("product_name") or "Unknown product"
        quantity = order.get("quantity") or 0
        total_price = order.get("total_price", order.get("price", 0))
        hall = order.get("hall") or "N/A"
        room_number = order.get("room_number") or "N/A"
        delivery_window = order.get("delivery_window") or "N/A"
        status = order.get("status") or "New"
        lines.append(
            f"{index}. ID: {order_id}\n"
            f"Product: {product_name}\n"
            f"Quantity: {quantity}\n"
            f"Total: {_format_amount(total_price)}\n"
            f"Location: {hall}, Room {room_number}\n"
            f"Time: {delivery_window}\n"
            f"Status: {status}"
        )

    await update.message.reply_text(
        "\n\n".join(lines),
        reply_markup=get_reply_keyboard(),
    )

async def cmd_payment_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Payment History feature is coming soon.",
        reply_markup=get_reply_keyboard(),
    )

async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 For help, contact us on Telegram: +234 70 8471 0152",
        reply_markup=get_reply_keyboard(),
    )

async def cmd_pay_for_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pay_for_products(update, context)


async def cmd_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return
    await update.message.reply_text(
        f"chat_id: {chat.id}\nchat_type: {chat.type}",
        reply_markup=get_reply_keyboard(),
    )


async def cmd_test_orders_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = os.getenv("ORDERS_GROUP_CHAT_ID", "").strip()
    if not raw:
        await update.message.reply_text(
            "ORDERS_GROUP_CHAT_ID is not set in the bot environment.",
            reply_markup=get_reply_keyboard(),
        )
        return
    try:
        chat_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            f"ORDERS_GROUP_CHAT_ID must be an integer; got: {raw!r}",
            reply_markup=get_reply_keyboard(),
        )
        return

    try:
        await context.bot.send_message(chat_id=chat_id, text="✅ Test: orders group posting works.")
    except Exception as exc:
        logger.exception("Failed to send test message to orders group")
        await update.message.reply_text(
            f"Failed to send to orders group. Error: {type(exc).__name__}",
            reply_markup=get_reply_keyboard(),
        )
        return

    await update.message.reply_text(
        f"Sent test message to orders group chat_id={chat_id}.",
        reply_markup=get_reply_keyboard(),
    )

# --------------------------
# /start Handler
# --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🛒 I'm a Buyer", callback_data="start_buyer")]]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋 Welcome! Choose a role to continue:", reply_markup=markup)

# --------------------------
# Global Error Handler
# --------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error", exc_info=context.error)
    try:
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.answer(
                "An error occurred. Please try again.", show_alert=True
            )
    except Exception:
        logger.exception("Failed to answer callback after error")

# --------------------------
# Async function to set menu commands
# --------------------------
async def set_bot_commands(app):
    await app.bot.set_my_commands(get_bot_commands())

# --------------------------
# Main
# --------------------------
def main():
    app = ApplicationBuilder().token(TOKEN).post_init(set_bot_commands).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("viewcart", cmd_view_cart))
    app.add_handler(CommandHandler("paymenthistory", cmd_payment_history))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("payforproducts", cmd_pay_for_products))
    app.add_handler(CommandHandler("chatid", cmd_chat_id))
    app.add_handler(CommandHandler("testordersgroup", cmd_test_orders_group))
    app.add_handler(CommandHandler("debugstate", debug_state))
    app.add_handler(CommandHandler("debugproducts", debug_products))
    app.add_handler(MessageHandler(filters.Regex(r"^My Orders$"), btn_my_orders))
    app.add_handler(MessageHandler(filters.Regex(r"^Help$"), cmd_support))

    # Add buyer conversation handler
    app.add_handler(get_buyer_conversation())


    # Global error handler
    app.add_error_handler(error_handler)



    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
