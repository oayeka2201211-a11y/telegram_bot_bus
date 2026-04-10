# bots/buyer_bot.py

import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from utils import database as db
from utils.logger import logger
from utils.menu import get_reply_keyboard

# Conversation states
EMAIL, PHONE, CATEGORY, SUBCATEGORY, PRODUCT, ORDER_QUANTITY, ORDER_NAME, ORDER_HALL, ORDER_ROOM, DELIVERY_TIME, ORDER_CONFIRM = range(11)
PAYMENT_REF_STATE = "PAYMENT_REF"
ORDER_DRAFT_KEY = "pending_order"

CATEGORY_MAP = {
    "Snacks": [],
    "Fashion & Style": ["Accessories", "Bags", "Clothing", "Footwear", "Sport Casual"],
    "Gadgets": ["Ipods", "Tablets & Ipads", "Laptops", "Accessories"],
    "Beauty & Personal care": ["Fragrance", "Hair Attachment", "Hair care", "Cosmetics"],
    "Books": ["Journals"],
    "Babes & Gifts": ["Customized Gifts", "Gift Boxes", "Money bouqet", "Crochet Bouqet"],
    "Sport & Fitness": ["Jerseys", "Gym Shorts"]
}

DELIVERY_WINDOWS = [
    "Today 5–6 PM",
    "Today 7–8 PM",
    "Tomorrow 5–6 PM",
]


def _load_cart(buyer_id: int):
    # Prefer deterministic cart key (telegram_id) for atomic updates.
    cart = db.cart_collection.get_by_key(str(buyer_id))
    if cart:
        return cart

    # Fallback to legacy carts stored under push IDs.
    legacy = db.cart_collection.find_one({"telegram_id": buyer_id})
    if not legacy:
        return None

    legacy_id = legacy.get("_id")
    if legacy_id and str(legacy_id) != str(buyer_id):
        # Migrate legacy cart to deterministic key for safer updates.
        data = dict(legacy)
        data.pop("_id", None)
        data.pop("id", None)
        db.cart_collection.set_by_key(str(buyer_id), data)
        db.cart_collection.delete_one({"_id": legacy_id})
        return db.cart_collection.get_by_key(str(buyer_id))

    return legacy


def _normalize_text(value):
    return str(value or "").strip().lower()


def _coerce_text_list(value):
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            if isinstance(item, str) and item.strip():
                values.append(item.strip())
        return values
    return []


def _extract_seller_categories(item):
    categories = []
    for key in ("main_category", "category", "categories", "product_category"):
        categories.extend(_coerce_text_list(item.get(key)))
    return categories


def _extract_product_name(item):
    for key in ("name", "product_name", "title", "productTitle", "label"):
        value = item.get(key)
        if value:
            return str(value).strip()
    return "Unnamed Product"


def _extract_product_price(item):
    raw_price = item.get("price", item.get("amount", 0))
    if isinstance(raw_price, (int, float)):
        return float(raw_price)

    cleaned = "".join(ch for ch in str(raw_price) if ch.isdigit() or ch == ".")
    if not cleaned:
        return 0.0

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _format_naira(amount):
    value = float(amount or 0)
    if value.is_integer():
        return f"₦{int(value):,}"
    return f"₦{value:,.2f}"


def _extract_product_description(item):
    for key in ("description", "details", "summary", "about"):
        value = item.get(key)
        if value:
            return str(value).strip()
    return "No extra details available."


def _extract_product_sku(item):
    for key in ("sku", "product_sku", "product_code", "code"):
        value = item.get(key)
        if value:
            return str(value).strip()
    return str(item.get("_id") or item.get("id") or "")


def _extract_product_image(item):
    for key in ("image_file_id", "image_file_ids", "file_id", "file_ids", "photo_file_id", "photo_file_ids"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first
            if isinstance(first, dict):
                for inner_key in ("file_id", "id", "url", "link"):
                    if first.get(inner_key):
                        return first.get(inner_key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            for inner_key in ("file_id", "id", "url", "link", "image"):
                if value.get(inner_key):
                    return value.get(inner_key)

    images = item.get("images") or item.get("image") or item.get("photos") or item.get("image_url") or item.get("imageUrl")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, str) and first.strip():
            return first
        if isinstance(first, dict):
            for inner_key in ("url", "link", "file_id", "src", "image", "id"):
                if first.get(inner_key):
                    return first.get(inner_key)
    if isinstance(images, dict):
        for inner_key in ("url", "link", "file_id", "src", "image", "id"):
            if images.get(inner_key):
                return images.get(inner_key)
    if isinstance(images, str) and images.strip():
        return images
    return None


def _extract_product_category(item):
    for category in _extract_product_categories(item):
        return category
    return ""


def _extract_product_categories(item):
    categories = []
    for key in (
        "category",
        "categories",
        "main_category",
        "product_category",
        "seller_category",
        "mainCategory",
        "productCategory",
    ):
        categories.extend(_coerce_text_list(item.get(key)))
    return categories


def _collect_available_categories():
    categories = []
    seen = set()

    for category in CATEGORY_MAP.keys():
        normalized = _normalize_text(category)
        if normalized not in seen:
            seen.add(normalized)
            categories.append(category)

    for seller in db.sellers_collection.find({}):
        for category in _extract_seller_categories(seller):
            normalized = _normalize_text(category)
            if category and normalized not in seen:
                seen.add(normalized)
                categories.append(category)

    for product in db.products.find({}):
        for category in _extract_product_categories(product):
            normalized = _normalize_text(category)
            if category and normalized not in seen:
                seen.add(normalized)
                categories.append(category)

    return categories or list(CATEGORY_MAP.keys())


def _products_for_category(category_name):
    selected = []
    normalized_category = _normalize_text(category_name)
    seller_names_in_category = {
        _normalize_text(seller.get("business_name"))
        for seller in db.sellers_collection.find({})
        if any(
            _normalize_text(item) == normalized_category
            for item in _extract_seller_categories(seller)
        ) and seller.get("business_name")
    }

    for product in db.products.find({}):
        product_categories = _extract_product_categories(product)
        if any(_normalize_text(item) == normalized_category for item in product_categories):
            selected.append(product)
            continue

        brand_name = _normalize_text(product.get("business_name") or product.get("brand"))
        if brand_name and brand_name in seller_names_in_category:
            selected.append(product)
    return selected


def _brands_for_category(category_name):
    brand_names = []
    seen = set()
    normalized_category = _normalize_text(category_name)

    for seller in db.sellers_collection.find({}):
        if any(_normalize_text(item) == normalized_category for item in _extract_seller_categories(seller)):
            brand_name = seller.get("business_name")
            if brand_name and brand_name not in seen:
                seen.add(brand_name)
                brand_names.append(brand_name)

    for product in _products_for_category(category_name):
        brand_name = product.get("business_name") or product.get("brand")
        if brand_name and brand_name not in seen:
            seen.add(brand_name)
            brand_names.append(brand_name)

    return brand_names


def _delivery_option_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(option, callback_data=f"delivery::{option}")]
        for option in DELIVERY_WINDOWS
    ])


def _build_order_summary(order_data):
    return (
        "Order summary:\n"
        f"– Product: {order_data.get('product_name')}\n"
        f"– Quantity: {order_data.get('quantity')}\n"
        f"– Total price: {_format_naira(order_data.get('total_price'))}\n"
        f"– Hall: {order_data.get('hall')}\n"
        f"– Room number: {order_data.get('room_number')}\n"
        f"– Time: {order_data.get('delivery_window')}\n"
        "Confirm?"
    )


def _generate_delivery_message(window_label):
    if window_label.startswith("Today"):
        return window_label.replace("Today ", "")
    return window_label

# --------------------------
# Entry / Registration
# --------------------------
async def start_buyer_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        # acknowledge the callback to remove loading indicator and give feedback
        try:
            await query.answer(text="Opening buyer flow...")
        except Exception:
            # fallback to a plain answer if the text answer fails
            try:
                await query.answer()
            except Exception:
                logger.exception("Failed to answer callback_query")
        # attempt to edit the originating message to show immediate feedback
        try:
            await query.message.edit_text("🔄 Starting buyer flow...")
        except Exception:
            # ignore if message cannot be edited (e.g., old messages or channels)
            pass

    msg = query.message if query else update.message

    logger.info(f"start_buyer_flow triggered for user {update.effective_user.id}")

    context.user_data.clear()
    context.chat_data.clear()
    context.user_data.pop("awaiting_payment_ref", None)
    context.chat_data["conversation_active"] = True

    try:
        existing = db.buyers_collection.find_one({"telegram_id": update.effective_user.id})
    except Exception:
        logger.exception("DB lookup failed in start_buyer_flow")
        await msg.reply_text(
            "❌ An internal error occurred. Please try again later.",
            reply_markup=get_reply_keyboard(),
        )
        return ConversationHandler.END

    if existing:
        await msg.reply_text(
            f"👋 Hi {update.effective_user.first_name or 'there'} — you're already registered.",
            reply_markup=get_reply_keyboard(),
        )
        await msg.reply_text(
            "Use the reply keyboard below for the same commands in the bot menu.",
            reply_markup=get_reply_keyboard(),
        )
        return ConversationHandler.END

    await msg.reply_text(
        "🛒 Buyer registration — please enter your email address:",
        reply_markup=get_reply_keyboard(),
    )
    return EMAIL


async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("Invalid email. Please enter a valid email address:")
        return EMAIL

    context.user_data["email_address"] = email
    await update.message.reply_text("Please enter your Telegram phone number:")
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 7:
        await update.message.reply_text("Invalid phone. Please enter a valid phone number:")
        return PHONE

    normalized = ("+" + digits) if not phone.startswith("+") else phone
    context.user_data["telegram_phone_number"] = normalized

    payload = {
        "telegram_id": update.effective_user.id,
        "telegram_username": update.effective_user.username,
        "email_address": context.user_data.get("email_address"),
        "telegram_phone_number": context.user_data.get("telegram_phone_number"),
        "last_active": datetime.utcnow(),
    }

    try:
        db.buyers_collection.insert_one(payload)
        await update.message.reply_text(
            "✅ Registration complete — welcome!",
            reply_markup=get_reply_keyboard(),
        )
    except Exception:
        logger.exception("Failed to save buyer to DB")
        await update.message.reply_text(
            "❌ Couldn't save your info. Try again later.",
            reply_markup=get_reply_keyboard(),
        )
        context.user_data.clear()
        context.chat_data.clear()
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        f"👋 Hi {update.effective_user.first_name or 'there'} — welcome!",
        reply_markup=get_reply_keyboard(),
    )
    await update.message.reply_text(
        "Use the reply keyboard below for the same commands in the bot menu.",
        reply_markup=get_reply_keyboard(),
    )
    return ConversationHandler.END


# --------------------------
# Category Menu
# --------------------------
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg = query.message if query else update.message

    categories = _collect_available_categories()
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat::{cat}")] for cat in categories]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.answer()
        try:
            await msg.edit_text("🛍️ Choose a category:", reply_markup=markup)
        except:
            await msg.reply_text("🛍️ Choose a category:", reply_markup=markup)
    else:
        await msg.reply_text("🛍️ Choose a category:", reply_markup=markup)

    context.chat_data["conversation_active"] = True
    context.chat_data["current_state"] = "CATEGORY"
    return CATEGORY


async def start_place_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for placing an order (checks registration then shows categories)."""
    query = update.callback_query
    msg = query.message if query else update.message

    if query:
        try:
            await query.answer()
        except Exception:
            pass

    try:
        existing = db.buyers_collection.find_one({"telegram_id": update.effective_user.id})
    except Exception:
        logger.exception("DB lookup failed in start_place_order")
        await msg.reply_text("❌ An internal error occurred. Please try again later.")
        return ConversationHandler.END

    if not existing:
        await msg.reply_text(
            "⚠️ You need to register first. Use /buyer to register.",
            reply_markup=get_reply_keyboard(),
        )
        return ConversationHandler.END

    context.user_data.pop("awaiting_payment_ref", None)
    context.user_data.pop(ORDER_DRAFT_KEY, None)
    return await show_categories(update, context)


async def _render_products_list(message, products, heading, back_callback):
    if not products:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅ Back", callback_data=back_callback)]
        ])
        await message.edit_text(
            f"ℹ️ No products found for {heading}.",
            reply_markup=markup,
        )
        return PRODUCT

    try:
        await message.edit_text(f"🛒 {heading}")
    except Exception:
        pass

    for index, item in enumerate(products, start=1):
        item_id = str(item.get("_id") or item.get("id"))
        name = _extract_product_name(item)
        price = _extract_product_price(item)
        image = _extract_product_image(item)
        caption = f"{index}. {name} – {_format_naira(price)}"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("View details", callback_data=f"details::{item_id}"),
                InlineKeyboardButton("Order this", callback_data=f"order::{item_id}"),
            ]
        ])

        try:
            if image:
                await message.reply_photo(photo=image, caption=caption, reply_markup=keyboard)
            else:
                await message.reply_text(caption, reply_markup=keyboard)
        except Exception:
            await message.reply_text(caption, reply_markup=keyboard)

    await message.reply_text(
        "Choose a product option above, or go back below.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅ Back", callback_data=back_callback)]
        ]),
    )
    return PRODUCT


# --------------------------
# Category → Brand List
# --------------------------
async def show_subcategories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("cat::"):
        selected_category = query.data.replace("cat::", "")
    else:
        selected_category = context.user_data.get("selected_category")

    if not selected_category:
        await query.message.reply_text("⚠️ Please choose a category first.")
        return CATEGORY

    context.user_data["selected_category"] = selected_category

    if _normalize_text(selected_category) == "snacks":
        products = _products_for_category(selected_category)
        context.chat_data["current_state"] = "PRODUCT"
        return await _render_products_list(
            query.message,
            products,
            f"Products in {selected_category}",
            "nav::back_to_categories",
        )

    brands = _brands_for_category(selected_category)
    if not brands:
        products = _products_for_category(selected_category)
        context.chat_data["current_state"] = "PRODUCT"
        return await _render_products_list(
            query.message,
            products,
            f"Products in {selected_category}",
            "nav::back_to_categories",
        )

    keyboard = [
        [InlineKeyboardButton(brand_name, callback_data=f"brand::{brand_name}")]
        for brand_name in brands
    ]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="nav::back_to_categories")])
    markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(f"🏬 Brands for {selected_category}:", reply_markup=markup)
    context.chat_data["current_state"] = "SUBCATEGORY"
    return SUBCATEGORY


# --------------------------
# Brand → Products
# --------------------------
async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_brand = query.data.replace("brand::", "")
    selected_category = context.user_data.get("selected_category")
    context.user_data["selected_brand"] = selected_brand

    products = []
    for item in db.products.find({"business_name": selected_brand}):
        product_category = _extract_product_category(item)
        if selected_category and product_category and _normalize_text(product_category) != _normalize_text(selected_category):
            continue
        products.append(item)

    context.chat_data["current_state"] = "PRODUCT"
    return await _render_products_list(
        query.message,
        products,
        f"Products from {selected_brand}",
        "nav::back_to_subcategory",
    )


# --------------------------
# Product details
# --------------------------
async def show_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = query.data.replace("details::", "")

    product = db.products.find_one({"_id": item_id})
    if not product:
        await query.message.reply_text("❌ Product not found.")
        return PRODUCT

    details_message = (
        f"{_extract_product_name(product)}\n"
        f"Brand: {product.get('business_name') or product.get('brand') or 'N/A'}\n"
        f"Category: {_extract_product_category(product) or context.user_data.get('selected_category', 'N/A')}\n"
        f"SKU: {_extract_product_sku(product)}\n"
        f"Price: {_format_naira(_extract_product_price(product))}\n"
        f"Details: {_extract_product_description(product)}"
    )
    await query.message.reply_text(details_message)
    return PRODUCT


# --------------------------
# Start order flow
# --------------------------
async def start_order_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = query.data.replace("order::", "")
    product = db.products.find_one({"_id": item_id})

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return PRODUCT

    context.user_data[ORDER_DRAFT_KEY] = {
        "product_id": item_id,
        "product_name": _extract_product_name(product),
        "product_sku": _extract_product_sku(product),
        "unit_price": _extract_product_price(product),
        "brand": product.get("business_name") or product.get("brand") or "",
        "category": _extract_product_category(product) or context.user_data.get("selected_category", ""),
    }

    await query.message.reply_text("How many would you like to order?")
    context.chat_data["current_state"] = "ORDER_QUANTITY"
    return ORDER_QUANTITY


async def get_order_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity_text = update.message.text.strip()
    if not quantity_text.isdigit() or int(quantity_text) <= 0:
        await update.message.reply_text("Please enter a valid quantity as a number.")
        return ORDER_QUANTITY

    order_data = context.user_data.get(ORDER_DRAFT_KEY)
    if not order_data:
        await update.message.reply_text("⚠️ No active order. Tap 'Order this' again.")
        return ConversationHandler.END

    quantity = int(quantity_text)
    order_data["quantity"] = quantity
    order_data["total_price"] = order_data.get("unit_price", 0) * quantity
    await update.message.reply_text("Please enter your name:")
    context.chat_data["current_state"] = "ORDER_NAME"
    return ORDER_NAME


async def get_order_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student_name = update.message.text.strip()
    if len(student_name) < 2:
        await update.message.reply_text("Please enter a valid name.")
        return ORDER_NAME

    order_data = context.user_data.get(ORDER_DRAFT_KEY)
    if not order_data:
        await update.message.reply_text("⚠️ No active order. Tap 'Order this' again.")
        return ConversationHandler.END

    order_data["student_name"] = student_name
    await update.message.reply_text("Which hall should we deliver to?")
    context.chat_data["current_state"] = "ORDER_HALL"
    return ORDER_HALL


async def get_order_hall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hall = update.message.text.strip()
    if len(hall) < 2:
        await update.message.reply_text("Please enter a valid hall.")
        return ORDER_HALL

    order_data = context.user_data.get(ORDER_DRAFT_KEY)
    if not order_data:
        await update.message.reply_text("⚠️ No active order. Tap 'Order this' again.")
        return ConversationHandler.END

    order_data["hall"] = hall
    await update.message.reply_text("What is your room number?")
    context.chat_data["current_state"] = "ORDER_ROOM"
    return ORDER_ROOM


async def get_order_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room_number = update.message.text.strip()
    if len(room_number) < 1:
        await update.message.reply_text("Please enter a valid room number.")
        return ORDER_ROOM

    order_data = context.user_data.get(ORDER_DRAFT_KEY)
    if not order_data:
        await update.message.reply_text("⚠️ No active order. Tap 'Order this' again.")
        return ConversationHandler.END

    order_data["room_number"] = room_number
    await update.message.reply_text(
        "Choose your preferred delivery time:",
        reply_markup=_delivery_option_buttons(),
    )
    context.chat_data["current_state"] = "DELIVERY_TIME"
    return DELIVERY_TIME


async def choose_delivery_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delivery_window = query.data.replace("delivery::", "")

    order_data = context.user_data.get(ORDER_DRAFT_KEY)
    if not order_data:
        await query.message.reply_text("⚠️ No active order. Tap 'Order this' again.")
        return ConversationHandler.END

    order_data["delivery_window"] = delivery_window
    summary = _build_order_summary(order_data)
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirm", callback_data="order_confirm"),
            InlineKeyboardButton("Cancel", callback_data="order_cancel"),
        ]
    ])
    await query.message.reply_text(summary, reply_markup=markup)
    context.chat_data["current_state"] = "ORDER_CONFIRM"
    return ORDER_CONFIRM


async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.exception("Failed to remove confirm/cancel buttons for order")
    order_data = context.user_data.get(ORDER_DRAFT_KEY)
    if not order_data:
        await query.message.reply_text("⚠️ No active order found.")
        return ConversationHandler.END

    created_at = datetime.utcnow().isoformat()
    payload = {
        "date_time": created_at,
        "student_name": order_data.get("student_name"),
        "telegram_username": update.effective_user.username,
        "telegram_id": update.effective_user.id,
        "hall": order_data.get("hall"),
        "room_number": order_data.get("room_number"),
        "hall_room": f"{order_data.get('hall')} / {order_data.get('room_number')}",
        "product_sku": order_data.get("product_sku"),
        "product_id": order_data.get("product_id"),
        "product_name": order_data.get("product_name"),
        "brand": order_data.get("brand"),
        "category": order_data.get("category"),
        "quantity": order_data.get("quantity"),
        "price": order_data.get("unit_price"),
        "total_price": order_data.get("total_price"),
        "delivery_window": order_data.get("delivery_window"),
        "status": "New",
    }

    try:
        result = db.orders_collection.insert_one(payload)
        order_id = f"ORD-{str(result.inserted_id)[-6:].upper()}"
        db.orders_collection.update_one({"_id": result.inserted_id}, {"$set": {"order_id": order_id}})
    except Exception:
        logger.exception("Failed to save order")
        await query.message.reply_text("❌ Could not save your order right now. Please try again.")
        context.user_data.pop(ORDER_DRAFT_KEY, None)
        return ConversationHandler.END

    orders_group_chat_id_raw = os.getenv("ORDERS_GROUP_CHAT_ID", "").strip()
    if orders_group_chat_id_raw:
        try:
            orders_group_chat_id = int(orders_group_chat_id_raw)
            group_message = (
                "🆕 New order received\n"
                f"ID: {order_id}\n"
                f"Student: {payload.get('student_name') or 'N/A'}\n"
                f"User: @{payload.get('telegram_username') or 'N/A'} (ID: {payload.get('telegram_id')})\n"
                f"Product: {payload.get('product_name')}\n"
                f"Qty: {payload.get('quantity')}\n"
                f"Total: {_format_naira(payload.get('total_price'))}\n"
                f"Location: {payload.get('hall')} / {payload.get('room_number')}\n"
                f"Time: {payload.get('delivery_window')}\n"
                f"Created: {payload.get('date_time')}"
            )
            await context.bot.send_message(chat_id=orders_group_chat_id, text=group_message)
            logger.info(f"Posted order {order_id} to orders group chat_id={orders_group_chat_id}")
        except ValueError:
            logger.error(
                "ORDERS_GROUP_CHAT_ID must be an integer chat id; "
                f"got: {orders_group_chat_id_raw!r}"
            )
        except Exception:
            logger.exception("Failed to send order to orders group chat")
    else:
        logger.warning("ORDERS_GROUP_CHAT_ID not set; skipping group notification")

    context.user_data.pop(ORDER_DRAFT_KEY, None)
    context.chat_data["current_state"] = None
    await query.message.reply_text(
        f"Thank you. Your order (ID: {order_id}) has been received. We will deliver between {_generate_delivery_message(order_data.get('delivery_window', 'the selected time window'))}.",
        reply_markup=get_reply_keyboard(),
    )
    return ConversationHandler.END


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.exception("Failed to remove confirm/cancel buttons for cancelled order")
    context.user_data.pop(ORDER_DRAFT_KEY, None)
    context.chat_data["current_state"] = "PRODUCT"
    await query.message.reply_text("❌ Order cancelled.")
    return PRODUCT


# --------------------------
# View Cart with Images
# --------------------------
async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buyer_id = update.effective_user.id
    cart = _load_cart(buyer_id)
    msg = update.message
    if msg is None and update.callback_query:
        msg = update.callback_query.message

    if not cart or not cart.get("products"):
        if msg:
            await msg.reply_text("🛒 Your cart is empty.", reply_markup=get_reply_keyboard())
        return

    for product in cart["products"]:
        name = product.get("name") or product.get("product_name") or "Unnamed Product"
        price = product.get("price", "N/A")
        stock = product.get("amount_in_stock", product.get("quantity", "N/A"))

        image = None
        images = product.get("images")
        if images is None:
            images = product.get("image_file_ids")
        if isinstance(images, list) and images:
            image = images[0]
        elif isinstance(images, str) and images.strip():
            image = images

        caption = (
            f"🛒 *{name}*\n"
            f"💵 Price: ₦{price}\n"
            f"📦 Stock: {stock}\n"
        )

        product_id = product.get("product_id")
        remove_key = product_id or name
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Remove from Cart", callback_data=f"removecart::{remove_key}")]
        ])

        if not msg:
            continue
        if image:
            await msg.reply_photo(photo=image, caption=caption, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await msg.reply_text(caption, reply_markup=keyboard, parse_mode="Markdown")


# --------------------------
# Remove from Cart
# --------------------------
async def remove_from_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buyer_id = update.effective_user.id
    remove_key = query.data.replace("removecart::", "")

    cart = _load_cart(buyer_id)
    if not cart:
        await query.message.reply_text("❌ Cart not found.", reply_markup=get_reply_keyboard())
        return

    def _matches_remove_key(p):
        if p.get("product_id") == remove_key:
            return True
        name = p.get("name") or p.get("product_name")
        return name == remove_key

    def _txn(existing):
        if not existing:
            return None
        products = existing.get("products") or []
        updated = [p for p in products if not _matches_remove_key(p)]
        if not updated:
            return None
        existing["products"] = updated
        existing["last_updated"] = datetime.utcnow().isoformat()
        return existing

    result = db.cart_collection.transaction(str(buyer_id), _txn)
    if result is None:
        await query.message.reply_text("🗑️ Your cart is now empty.", reply_markup=get_reply_keyboard())
        return

    # Refresh cart messages
    await view_cart(update, context)


# --------------------------
# Pay for Products Flow
# --------------------------
async def pay_for_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buyer_id = update.effective_user.id
    cart = _load_cart(buyer_id)
    if not cart or not cart.get("products"):
        await update.message.reply_text("🛒 Your cart is empty.", reply_markup=get_reply_keyboard())
        return

    total = sum([p.get("price", 0) for p in cart["products"]])
    context.user_data["awaiting_payment_ref"] = True
    context.chat_data["conversation_active"] = True
    context.chat_data["current_state"] = PAYMENT_REF_STATE
    await update.message.reply_text(
        f"💰 Total amount: ₦{total}\n"
        "Pay to Opay Account: 08012345678\n"
        "Account Name: Opay Store\n\n"
        "After payment, send me the payment reference ID.",
        reply_markup=get_reply_keyboard(),
    )


async def handle_payment_reference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_payment_ref"):
        return

    if context.chat_data.get("current_state") != PAYMENT_REF_STATE:
        return

    ref_id = update.message.text.strip()
    if not ref_id or len(ref_id) < 4 or len(ref_id) > 64:
        await update.message.reply_text(
            "⚠️ Please send a valid payment reference ID (4–64 characters).",
            reply_markup=get_reply_keyboard(),
        )
        return

    buyer_id = update.effective_user.id
    db.payments.insert_one({
        "telegram_id": buyer_id,
        "reference_id": ref_id,
        "paid": False,
        "created_at": datetime.utcnow()
    })
    context.user_data["awaiting_payment_ref"] = False
    context.chat_data["current_state"] = None
    await update.message.reply_text(
        "✅ Payment recorded. It will take one day to confirm payment "
        "and two days to deliver your products.",
        reply_markup=get_reply_keyboard(),
    )


# --------------------------
# Navigation / Cancel
# --------------------------
async def back_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    return await show_categories(update, context)


async def back_to_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    return await show_subcategories(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.chat_data.clear()
    await update.message.reply_text(
        "❌ Cancelled. Use /start to try again.",
        reply_markup=get_reply_keyboard(),
    )
    return ConversationHandler.END


# --------------------------
# Debug
# --------------------------
async def debug_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.chat_data.get("current_state", "None")
    active = context.chat_data.get("conversation_active", False)
    user_data = context.user_data
    await update.message.reply_text(
        f"State: {state}, Active: {active}, User data: {user_data}",
        reply_markup=get_reply_keyboard(),
    )


async def debug_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        all_products = list(db.products.find({}))
        snack_products = _products_for_category("Snacks")
    except Exception:
        logger.exception("Failed to fetch products in debug_products")
        await update.message.reply_text(
            "❌ Could not read products from the database.",
            reply_markup=get_reply_keyboard(),
        )
        return

    lines = [
        f"Total products visible: {len(all_products)}",
        f"Snack products matched: {len(snack_products)}",
    ]

    if not all_products:
        lines.append("No products were returned from the `products` collection.")
        await update.message.reply_text("\n".join(lines), reply_markup=get_reply_keyboard())
        return

    lines.extend(["", "First visible products:"])
    for index, product in enumerate(all_products[:5], start=1):
        lines.append(
            f"{index}. id={product.get('_id') or product.get('id') or 'N/A'} | "
            f"name={_extract_product_name(product)} | "
            f"category={product.get('category', 'N/A')} | "
            f"categories={product.get('categories', 'N/A')} | "
            f"price={product.get('price', product.get('amount', 'N/A'))} | "
            f"stock={product.get('amount_in_stock', 'N/A')}"
        )

    if snack_products:
        lines.extend(["", "Matched snacks:"])
        for index, product in enumerate(snack_products[:5], start=1):
            lines.append(
                f"{index}. id={product.get('_id') or product.get('id') or 'N/A'} | "
                f"name={_extract_product_name(product)} | "
                f"category={_extract_product_category(product) or 'N/A'} | "
                f"price={_extract_product_price(product)} | "
                f"stock={product.get('amount_in_stock', 'N/A')}"
            )

    await update.message.reply_text("\n".join(lines), reply_markup=get_reply_keyboard())


# --------------------------
# Conversation Handler
# --------------------------
def get_buyer_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler("buyer", start_buyer_flow),
            CommandHandler("placeorder", start_place_order),
            MessageHandler(filters.Regex(r"^Browse Products$"), start_place_order),
            CallbackQueryHandler(start_buyer_flow, pattern=r"^start_buyer$"),
            CallbackQueryHandler(start_place_order, pattern=r"^placeorder$"),
            CommandHandler("debugstate", debug_state),
            CommandHandler("viewcart", view_cart),
            CommandHandler("payforproducts", pay_for_products),
            CallbackQueryHandler(remove_from_cart, pattern=r"^removecart::.+$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payment_reference)
        ],
        states={
            EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_email),
                CallbackQueryHandler(start_buyer_flow, pattern=r"^start_buyer$"),
                CallbackQueryHandler(start_place_order, pattern=r"^placeorder$")
            ],
            PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone),
                CallbackQueryHandler(start_buyer_flow, pattern=r"^start_buyer$"),
                CallbackQueryHandler(start_place_order, pattern=r"^placeorder$")
            ],
            CATEGORY: [
                CallbackQueryHandler(show_subcategories, pattern=r"^cat::.+$"),
                CallbackQueryHandler(back_to_categories, pattern=r"^nav::back_to_categories$"),
                CallbackQueryHandler(start_buyer_flow, pattern=r"^start_buyer$"),
                CallbackQueryHandler(start_place_order, pattern=r"^placeorder$")
            ],
            SUBCATEGORY: [
                CallbackQueryHandler(show_products, pattern=r"^brand::.+$"),
                CallbackQueryHandler(back_to_categories, pattern=r"^nav::back_to_categories$"),
                CallbackQueryHandler(start_buyer_flow, pattern=r"^start_buyer$"),
                CallbackQueryHandler(start_place_order, pattern=r"^placeorder$")
            ],
            PRODUCT: [
                CallbackQueryHandler(show_product_details, pattern=r"^details::.+$"),
                CallbackQueryHandler(start_order_for_product, pattern=r"^order::.+$"),
                CallbackQueryHandler(back_to_subcategory, pattern=r"^nav::back_to_subcategory$"),
                CallbackQueryHandler(back_to_categories, pattern=r"^nav::back_to_categories$"),
                CallbackQueryHandler(start_buyer_flow, pattern=r"^start_buyer$"),
                CallbackQueryHandler(start_place_order, pattern=r"^placeorder$")
            ],
            ORDER_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_quantity),
            ],
            ORDER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_name),
            ],
            ORDER_HALL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_hall),
            ],
            ORDER_ROOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_room),
            ],
            DELIVERY_TIME: [
                CallbackQueryHandler(choose_delivery_time, pattern=r"^delivery::.+$"),
            ],
            ORDER_CONFIRM: [
                CallbackQueryHandler(confirm_order, pattern=r"^order_confirm$"),
                CallbackQueryHandler(cancel_order, pattern=r"^order_cancel$"),
            ]
        },

        fallbacks=[CommandHandler("cancel", cancel)],
        persistent=False
    )
