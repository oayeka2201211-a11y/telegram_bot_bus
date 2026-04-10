# Code Overview

This document explains how the Telegram bot works, how data flows through the system, and where database access happens. It is intended for teammates or new contributors who need a quick, accurate map of the codebase.

## What This Bot Does

This is a Telegram buyer bot that lets users register, browse sellers and products, add items to a cart, and submit a payment reference. It reads and writes data in Firebase Realtime Database.

## Main Entry Point

- `main_bot.py`
  - Loads the Telegram bot token from environment variables.
  - Registers command handlers and conversation handlers.
  - Starts polling.

## Buyer Flow Summary

1. User sends `/start`.
2. Bot shows role selection; user chooses Buyer.
3. Bot checks if buyer is already registered.
4. If not registered, bot collects email and phone and saves the buyer.
5. User places an order, picks category, seller, and product.
6. User can add products to cart, view cart, and submit payment reference.

## Key Files

- `main_bot.py`
  - Telegram bot setup and handler wiring.

- `bots/buyer_bot.py`
  - All buyer conversation logic: registration, category/seller/product selection, cart, and payment reference.

- `utils/database.py`
  - Firebase Realtime Database adapter.
  - Collection wrappers and read/write methods.

- `utils/logger.py`
  - Application logging.

## Database Access (Firebase Realtime DB)

Database operations are implemented through `utils/database.py`. The buyer flow in `bots/buyer_bot.py` calls these functions to read and write data.

### Reads (Checks and Lookups)

- Check if buyer exists:
  - `buyers_collection.find_one({"telegram_id": user_id})`
- Check if buyer can place order:
  - `buyers_collection.find_one({"telegram_id": user_id})`
- Load sellers for a category:
  - `sellers_collection.find({"main_category": category})`
  - fallback: `sellers_collection.find({"categories": category})`
- Load products for seller:
  - `products.find({"business_name": seller_name})`
- Load cart:
  - `cart_collection.find_one({"telegram_id": buyer_id})`

### Writes (Saves/Updates)

- Save new buyer registration:
  - `buyers_collection.insert_one(payload)`
- Add product to cart (existing cart):
  - `cart_collection.update_one({"telegram_id": buyer_id}, {"$set": {...}})`
- Add product to cart (new cart):
  - `cart_collection.insert_one({...})`
- Remove item or clear cart:
  - `cart_collection.update_one(...)` or `cart_collection.delete_one(...)`
- Save payment reference:
  - `payments.insert_one({"telegram_id": buyer_id, ...})`

## Environment Variables

These are loaded via `dotenv` in `main_bot.py` and `utils/database.py`.

- `MAIN_BOT_TOKEN`
  - Telegram Bot Token.

Firebase credentials (any one of these options):
- `FIREBASE_SERVICE_ACCOUNT`
  - Full JSON string or base64-encoded JSON for the service account.
- `GOOGLE_APPLICATION_CREDENTIALS`
  - File path to service account JSON.
- Local file fallback (repo root):
  - `zistack-76128-firebase-adminsdk-fbsvc-641b689f33.json`

Firebase DB URL:
- `FIREBASE_DATABASE_URL`
  - Overrides the default RTDB URL.
- If not set, URL is built from `FIREBASE_PROJECT_ID`.

## Conversation States

The buyer conversation states are defined in `bots/buyer_bot.py`:

- `EMAIL`
- `PHONE`
- `CATEGORY`
- `SUBCATEGORY`
- `PRODUCT`

## Notes for Contributors

- This project uses Firebase Realtime Database.
- All database logic is contained in `utils/database.py`.
- Buyer interaction logic is in `bots/buyer_bot.py`.
- Changes to data structure should keep the cart/product shape consistent.

