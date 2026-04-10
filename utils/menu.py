from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup


MENU_COMMANDS = [
    ("viewcart", "🛍️ View your cart"),
    ("placeorder", "🛒 Place an order"),
    ("paymenthistory", "💳 View payment history"),
    ("support", "📞 Contact customer support"),
    ("payforproducts", "💰 Pay for products"),
    ("chatid", "🆔 Show chat id"),
    ("testordersgroup", "✅ Test group post"),
    ("debugstate", "🔍 Debug conversation state"),
]


def get_bot_commands():
    return [BotCommand(command, description) for command, description in MENU_COMMANDS]


def get_reply_keyboard():
    keyboard = [
        [KeyboardButton("Browse Products"), KeyboardButton("My Orders")],
        [KeyboardButton("Help")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )
