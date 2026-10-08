---
name: sql-conventions
description: House SQL style for this repo -- keyword casing, join style, CTE usage, qualification rules. Use whenever writing or reviewing a SQL query for this project, whether hand-written or produced by an agent.
---

# SQL conventions

These apply to every query in this repo, and to SQL the metric compiler
(Phase 2) and agents (Phase 3+) produce -- the point of writing this down now
is that agent-generated SQL gets reviewed against the same bar as
hand-written SQL, not a looser one.

- **Keywords uppercase**, identifiers `snake_case`: `SELECT`, `FROM`, `WHERE`,
  `GROUP BY` -- matches Postgres/Olist's own naming and stays readable when
  this SQL gets posted into a Slack thread for a stakeholder to see.
- **Explicit `JOIN ... ON`**, never comma joins.
- **CTEs (`WITH ...`) over nested subqueries** once a query needs more than
  one logical step -- optimize for a human (or a reviewing agent) reading it
  top to bottom.
- **Always qualify columns with a table alias** once more than one table is
  in scope, even if a column name happens to be unambiguous today -- it stays
  unambiguous when the schema changes later.
- **Schema-qualify table names**: `raw.orders`, not `orders`, to avoid
  depending on `search_path`.
- **Explicit casts for date/timestamp comparisons and truncation**
  (`date_trunc('month', o.order_purchase_timestamp)`), even though the load
  script parses these as real `timestamp` columns -- be explicit about the
  grain you mean.
- **No implicit row-count surprises**: if a join can fan out (e.g. orders to
  order_items, or orders to order_payments), say so in a comment or structure
  the query (aggregate before joining) so the fan-out doesn't silently
  multiply a sum.
- **`LIMIT` when previewing**, never when the query is meant to return a
  complete answer -- guardrail row limits (Phase 6) are a separate,
  explicit mechanism, not something to pre-empt ad hoc.

## Example

```sql
WITH order_revenue AS (
    SELECT
        o.order_id,
        o.order_purchase_timestamp,
        SUM(oi.price) AS revenue
    FROM raw.orders AS o
    JOIN raw.order_items AS oi ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY o.order_id, o.order_purchase_timestamp
)
SELECT
    date_trunc('month', order_purchase_timestamp) AS month,
    SUM(revenue) AS monthly_revenue
FROM order_revenue
GROUP BY 1
ORDER BY 1;
```
