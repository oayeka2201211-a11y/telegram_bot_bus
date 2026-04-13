from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup


MENU_COMMANDS = [
    ("browseproducts", "🛍️ Browse products"),
    ("myorders", "📦 View your orders"),
    ("help", "🆘 Get help"),
    ("chatid", "🆔 Show chat id"),
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
