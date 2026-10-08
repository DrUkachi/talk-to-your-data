"""The semantic layer's models: grain, join graph, and available columns.

Deliberately code, not YAML -- getting a join right (and not fanning out a sum) is
business logic, not config. metrics.yaml references these by name; it can't define
new ones. See CLAUDE.md's "Data: Olist Brazilian e-commerce" section for why each
join/column choice here is what it is (e.g. customer_unique_id vs customer_id).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Model:
    name: str
    grain: str
    from_sql: str
    measures: dict[str, str]
    dimensions: dict[str, str]
    time_dimension: str
    base_filter: str | None = field(default=None)


MODELS: dict[str, Model] = {
    "order_items": Model(
        name="order_items",
        grain="one row per order line item",
        from_sql="""
            raw.order_items oi
            JOIN raw.orders o ON o.order_id = oi.order_id
            JOIN raw.products p ON p.product_id = oi.product_id
            LEFT JOIN raw.product_category_name_translation t
                ON t.product_category_name = p.product_category_name
        """,
        measures={
            "price": "oi.price",
            "freight_value": "oi.freight_value",
        },
        dimensions={
            "order_status": "o.order_status",
            # falls back to the Portuguese name rather than silently dropping the
            # ~2% of products with no English translation or no category at all
            "product_category": (
                "COALESCE(t.product_category_name_english, p.product_category_name)"
            ),
        },
        time_dimension="o.order_purchase_timestamp",
    ),
    "orders": Model(
        name="orders",
        grain="one row per order",
        from_sql="""
            raw.orders o
            JOIN raw.customers c ON c.customer_id = o.customer_id
        """,
        measures={
            # id-like columns, meant for count_distinct rather than sum/avg
            "order_id": "o.order_id",
            "customer_unique_id": "c.customer_unique_id",
        },
        dimensions={
            "order_status": "o.order_status",
            "customer_state": "c.customer_state",
        },
        time_dimension="o.order_purchase_timestamp",
    ),
    "order_reviews": Model(
        name="order_reviews",
        grain="one row per review record (review_id is not a unique key -- see CLAUDE.md)",
        from_sql="""
            raw.order_reviews r
            JOIN raw.orders o ON o.order_id = r.order_id
        """,
        measures={
            "review_score": "r.review_score",
        },
        dimensions={
            "order_status": "o.order_status",
        },
        # joins to orders for purchase timestamp (not review_creation_date) so every
        # model's time series lines up on the same calendar basis
        time_dimension="o.order_purchase_timestamp",
    ),
    "order_payments": Model(
        name="order_payments",
        grain="one row per payment installment/method on an order",
        from_sql="""
            raw.order_payments p
            JOIN raw.orders o ON o.order_id = p.order_id
        """,
        measures={
            "payment_value": "p.payment_value",
            "payment_installments": "p.payment_installments",
        },
        dimensions={
            "order_status": "o.order_status",
            "payment_type": "p.payment_type",
        },
        time_dimension="o.order_purchase_timestamp",
    ),
    "order_delivery": Model(
        name="order_delivery",
        grain="one row per delivered order (undelivered orders excluded, not null-propagated)",
        from_sql="raw.orders o",
        measures={
            "delivery_days": (
                "EXTRACT(DAY FROM (o.order_delivered_customer_date - o.order_purchase_timestamp))"
            ),
            "is_late": (
                "CASE WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date "
                "THEN 1.0 ELSE 0.0 END"
            ),
        },
        dimensions={
            "order_status": "o.order_status",
        },
        time_dimension="o.order_purchase_timestamp",
        base_filter="o.order_delivered_customer_date IS NOT NULL",
    ),
}
