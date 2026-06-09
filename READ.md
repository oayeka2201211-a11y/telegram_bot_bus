# ZiStack Telegram Bot — Setup Guide

## What this bot does
- Students register with name, phone, email (once)
- After registration → lands directly on the brand/menu list
- Brands listed (Mr. Dough + future brands)
- Student picks brand → sees menu → adds to cart
- Checkout → selects hostel + room number
- Sends bank transfer receipt (photo)
- ZiStack admin gets notified instantly with full order details + receipt

---

## Setup Steps

### 1. Create your bot
- Open Telegram → search `@BotFather`
- Send `/newbot` and follow the steps
- Copy your **Bot Token**

### 2. Get your Admin Chat ID
- Search `@userinfobot` on Telegram
- Send `/start` — it returns your Chat ID
- Copy it

### 3. Configure the bot
Open `bot.py` and fill in:
```python
BOT_TOKEN = "your_token_here"
ADMIN_CHAT_ID = "your_chat_id_here"

BANK_NAME = "Your Bank"
ACCOUNT_NAME = "Your Account Name"
ACCOUNT_NUMBER = "Your Account Number"
```

### 4. Add/edit brands & menu
This repo uses Firestore instead of a static `BRANDS` dictionary. Add/edit brands through the `sellers` and `products` collections:

`sellers/{sellerId}`
```json
{
  "business_name": "Mr. Dough",
  "tagline": "Premium Vanilla Custard Doughnuts",
  "verified": true
}
```

`products/{productId}`
```json
{
  "business_name": "Mr. Dough",
  "name": "Single Custard Doughnut",
  "price": 500,
  "description": "Premium Vanilla Custard Doughnut",
  "sku": "MRD-SINGLE",
  "image_file_id": "telegram_file_id_optional",
  "amount_in_stock": 20
}
```

To add another brand, create another `sellers` document and give its menu items the same `business_name`. The bot no longer uses categories; buyers see brands first, then products for the selected brand.

To seed the built-in Mr. Dough brand and starter menu into Firestore:
```bash
python scripts/seed_mr_dough.py
```

This creates or updates:
- `sellers/mr_dough`
- `products/mr_dough_single_custard_doughnut`
- `products/mr_dough_pack_of_3_doughnuts`

### 5. Install & run
```bash
pip install -r requirements.txt
python bot.py
```

### 6. Host it (so it runs 24/7)
Free options:
- **Railway.app** — easiest, free tier available
- **Render.com** — free background workers
- **PythonAnywhere** — student friendly

---

## Mr. Dough Channel Integration
In the Mr. Dough Telegram channel description/pinned message, add:
> "To order, visit our ZiStack store 👉 t.me/YourBotUsername"

That way students from Mr. Dough's channel are redirected to pay through ZiStack.

---

## Bot Commands
| Command | Action |
|---------|--------|
| `/start` | Register or return to menu |
| `/cancel` | Cancel current action |
