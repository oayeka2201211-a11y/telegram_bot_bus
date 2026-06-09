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


class _FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)


class _FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = _FakeMessage()

    async def answer(self, text=None):
        return None

    async def edit_message_reply_markup(self, reply_markup=None):
        return None


def _load_buyer_bot(sellers=None, products=None):
    fake_database = types.ModuleType("utils.database")

    class StockReservationError(Exception):
        def __init__(self, reason, available_stock=0, product_name="this product"):
            super().__init__(reason)
            self.reason = reason
            self.available_stock = available_stock
            self.product_name = product_name

    def reserve_stock_and_insert_order(product_id, quantity, order_payload):
        class Res: pass
        res = Res()
        res.inserted_id = "order123456"
        res.order_id = "ORD-123456"
        return res

    fake_database.sellers_collection = _FakeCollection(sellers)
    fake_database.products = _FakeCollection(products)
    fake_database.buyers_collection = _FakeCollection()
    fake_database.cart_collection = _FakeCollection()
    fake_database.orders_collection = _FakeCollection()
    fake_database.payments = _FakeCollection()
    fake_database.StockReservationError = StockReservationError
    fake_database.reserve_stock_and_insert_order = reserve_stock_and_insert_order

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


class BuyerBrandMatchingTests(unittest.TestCase):
    def test_collect_available_brands_combines_sellers_and_products(self):
        buyer_bot = _load_buyer_bot(
            sellers=[{"business_name": "Mr. Dough"}],
            products=[
                {"name": "Single Custard Doughnut", "business_name": "Mr. Dough", "price": 500},
                {"name": "Chapman", "brand": "Campus Drinks", "price": 800},
            ],
        )

        matches = buyer_bot._collect_available_brands()

        self.assertEqual(matches, ["Mr. Dough", "Campus Drinks"])

    def test_products_for_brand_matches_business_name_or_brand(self):
        buyer_bot = _load_buyer_bot(
            products=[
                {"name": "Single Custard Doughnut", "business_name": "Mr. Dough", "price": 500},
                {"name": "Pack of 3 Doughnuts", "brand": "Mr. Dough", "price": 1400},
                {"name": "Chapman", "brand": "Campus Drinks", "price": 800},
            ],
        )

        matches = buyer_bot._products_for_brand("Mr. Dough")

        self.assertEqual([item["name"] for item in matches], [
            "Single Custard Doughnut",
            "Pack of 3 Doughnuts",
        ])

    def test_out_of_stock_product_is_blocked_before_quantity_prompt(self):
        buyer_bot = _load_buyer_bot()
        buyer_bot.db.products.find_one = lambda query: {
            "_id": "abc",
            "name": "Single Custard Doughnut",
            "business_name": "Mr. Dough",
            "price": 500,
            "amount_in_stock": 0,
        }
        query = _FakeCallbackQuery("order::abc")
        update = types.SimpleNamespace(callback_query=query)
        context = types.SimpleNamespace(user_data={}, chat_data={})

        async def run():
            return await buyer_bot.start_order_for_product(update, context)

        import asyncio
        outcome = asyncio.run(run())

        self.assertEqual(outcome, buyer_bot.PRODUCT)
        self.assertTrue(query.message.replies)
        self.assertIn("out of stock", query.message.replies[0].lower())
        self.assertNotIn("pending_order", context.user_data)

    def test_quantity_over_stock_is_rejected(self):
        buyer_bot = _load_buyer_bot()
        message = _FakeMessage()
        message.text = "3"
        update = types.SimpleNamespace(message=message)
        context = types.SimpleNamespace(
            user_data={
                "pending_order": {
                    "product_name": "Single Custard Doughnut",
                    "unit_price": 500,
                    "available_stock": 2,
                }
            },
            chat_data={},
        )

        async def run():
            return await buyer_bot.get_order_quantity(update, context)

        import asyncio
        outcome = asyncio.run(run())

        self.assertEqual(outcome, buyer_bot.ORDER_QUANTITY)
        self.assertTrue(message.replies)
        self.assertIn("only 2 left", message.replies[0].lower())
        self.assertNotIn("quantity", context.user_data["pending_order"])

    def test_confirm_order_rejects_product_that_became_out_of_stock(self):
        buyer_bot = _load_buyer_bot()

        def reserve_stock_and_insert_order(product_id, quantity, order_payload):
            raise buyer_bot.db.StockReservationError("out_of_stock", 0, "Single Custard Doughnut")

        buyer_bot.db.reserve_stock_and_insert_order = reserve_stock_and_insert_order
        query = _FakeCallbackQuery("order_confirm")
        update = types.SimpleNamespace(
            callback_query=query,
            effective_user=types.SimpleNamespace(id=123, username="buyer"),
        )
        context = types.SimpleNamespace(
            user_data={
                "pending_order": {
                    "product_id": "abc",
                    "product_name": "Single Custard Doughnut",
                    "product_sku": "SKU-1",
                    "unit_price": 500,
                    "brand": "Mr. Dough",
                    "quantity": 1,
                    "total_price": 500,
                    "student_name": "Ada",
                    "hall": "A Hall",
                    "room_number": "101",
                    "delivery_window": "10am",
                }
            },
            chat_data={},
        )

        async def run():
            return await buyer_bot.confirm_order(update, context)

        import asyncio
        outcome = asyncio.run(run())

        self.assertEqual(outcome, buyer_bot.PRODUCT)
        self.assertTrue(query.message.replies)
        self.assertIn("out of stock", query.message.replies[0].lower())
        self.assertNotIn("pending_order", context.user_data)

    def test_confirm_order_rejects_quantity_when_stock_changed(self):
        buyer_bot = _load_buyer_bot()

        def reserve_stock_and_insert_order(product_id, quantity, order_payload):
            raise buyer_bot.db.StockReservationError("insufficient_stock", 2, "Single Custard Doughnut")

        buyer_bot.db.reserve_stock_and_insert_order = reserve_stock_and_insert_order
        query = _FakeCallbackQuery("order_confirm")
        update = types.SimpleNamespace(
            callback_query=query,
            effective_user=types.SimpleNamespace(id=123, username="buyer"),
        )
        context = types.SimpleNamespace(
            user_data={
                "pending_order": {
                    "product_id": "abc",
                    "product_name": "Single Custard Doughnut",
                    "product_sku": "SKU-1",
                    "unit_price": 500,
                    "brand": "Mr. Dough",
                    "quantity": 3,
                    "total_price": 1500,
                    "student_name": "Ada",
                    "hall": "A Hall",
                    "room_number": "101",
                    "delivery_window": "10am",
                }
            },
            chat_data={},
        )

        async def run():
            return await buyer_bot.confirm_order(update, context)

        import asyncio
        outcome = asyncio.run(run())

        self.assertEqual(outcome, buyer_bot.PRODUCT)
        self.assertTrue(query.message.replies)
        self.assertIn("only 2 left", query.message.replies[0].lower())
        self.assertNotIn("pending_order", context.user_data)


if __name__ == "__main__":
    unittest.main()
