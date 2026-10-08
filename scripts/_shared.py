"""Filename <-> table-name mapping shared by fetch_data.py and load_data.py."""

from pathlib import Path

EXPECTED_FILES = [
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
]


def table_name_for(filename: str) -> str:
    """olist_order_items_dataset.csv -> order_items; passes through unwrapped names."""
    stem = Path(filename).stem
    if stem.startswith("olist_") and stem.endswith("_dataset"):
        return stem[len("olist_") : -len("_dataset")]
    return stem
