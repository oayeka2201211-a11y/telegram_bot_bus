from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import database as db


BRAND_ID = "mr_dough"
BRAND_NAME = "Mr. Dough"

BRAND = {
    "business_name": BRAND_NAME,
    "tagline": "Premium Vanilla Custard Doughnuts",
    "role": "seller",
    "verified": True,
}

PRODUCTS = [
    {
        "id": "mr_dough_single_custard_doughnut",
        "name": "Single Custard Doughnut",
        "price": 500,
        "description": "Premium Vanilla Custard Doughnut",
        "sku": "MRD-SINGLE",
        "amount_in_stock": 20,
    },
    {
        "id": "mr_dough_pack_of_3_doughnuts",
        "name": "Pack of 3 Doughnuts",
        "price": 1400,
        "description": "Pack of 3 Premium Vanilla Custard Doughnuts",
        "sku": "MRD-PACK-3",
        "amount_in_stock": 20,
    },
]


def main():
    now = datetime.now(UTC).isoformat()

    db.sellers_collection.set_by_key(
        BRAND_ID,
        {
            **BRAND,
            "_id": BRAND_ID,
            "id": BRAND_ID,
            "updated_at": now,
        },
    )

    for product in PRODUCTS:
        product_id = product["id"]
        payload = {
            **product,
            "_id": product_id,
            "business_name": BRAND_NAME,
            "brand": BRAND_NAME,
            "updated_at": now,
        }
        db.products_collection.set_by_key(product_id, payload)

    print(f"Seeded {BRAND_NAME} with {len(PRODUCTS)} products.")


if __name__ == "__main__":
    main()
