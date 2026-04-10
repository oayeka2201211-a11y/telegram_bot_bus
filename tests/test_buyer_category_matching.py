import importlib
import sys
import types
import unittest
from unittest import mock


class _FakeCollection:
    def __init__(self, items=None):
        self.items = list(items or [])

    def find(self, query):
        return list(self.items)

    def find_one(self, query):
        return None

    def get_by_key(self, key):
        return None


def _load_buyer_bot(sellers=None, products=None):
    fake_database = types.ModuleType("utils.database")
    fake_database.sellers_collection = _FakeCollection(sellers)
    fake_database.products = _FakeCollection(products)
    fake_database.buyers_collection = _FakeCollection()
    fake_database.cart_collection = _FakeCollection()
    fake_database.orders_collection = _FakeCollection()
    fake_database.payments = _FakeCollection()

    fake_telegram = types.ModuleType("telegram")
    fake_telegram.Update = object
    fake_telegram.InlineKeyboardMarkup = object
    fake_telegram.InlineKeyboardButton = object

    fake_ext = types.ModuleType("telegram.ext")
    fake_ext.ConversationHandler = types.SimpleNamespace(END=-1)
    fake_ext.CommandHandler = object
    fake_ext.MessageHandler = object
    fake_ext.CallbackQueryHandler = object
    fake_ext.filters = types.SimpleNamespace(TEXT=object(), COMMAND=object(), Regex=lambda pattern: pattern)
    fake_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)

    fake_logger = types.ModuleType("utils.logger")
    fake_logger.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )

    fake_menu = types.ModuleType("utils.menu")
    fake_menu.get_reply_keyboard = lambda: None

    with mock.patch.dict(
        sys.modules,
        {
            "utils.database": fake_database,
            "telegram": fake_telegram,
            "telegram.ext": fake_ext,
            "utils.logger": fake_logger,
            "utils.menu": fake_menu,
        },
    ):
        sys.modules.pop("bots.buyer_bot", None)
        module = importlib.import_module("bots.buyer_bot")
        return importlib.reload(module)


class BuyerCategoryMatchingTests(unittest.TestCase):
    def test_products_for_category_falls_back_to_seller_category(self):
        buyer_bot = _load_buyer_bot(
            sellers=[{"business_name": "Crunchies", "categories": ["Snacks"]}],
            products=[{"name": "Plantain Chips", "business_name": "Crunchies", "price": 1500}],
        )

        matches = buyer_bot._products_for_category("Snacks")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["name"], "Plantain Chips")

    def test_extract_product_categories_supports_lists_and_alt_keys(self):
        buyer_bot = _load_buyer_bot()

        categories = buyer_bot._extract_product_categories(
            {
                "categories": ["Snacks", "Drinks"],
                "mainCategory": "Food",
                "productCategory": "Campus Specials",
            }
        )

        self.assertEqual(categories, ["Snacks", "Drinks", "Food", "Campus Specials"])


if __name__ == "__main__":
    unittest.main()
