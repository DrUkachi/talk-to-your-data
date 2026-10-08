---
name: eda-profiling
description: Profile one or more raw.* Postgres tables -- row counts, null rates, distinct counts, numeric/date ranges, and anomaly flags. Use when asked to explore, profile, or characterize the loaded Olist data, or before trusting a table you haven't queried yet this session.
---

# EDA / profiling

Query the live Postgres database directly -- never load the source CSVs into
pandas for this. The DB is the source of truth for current state, and
querying it (instead of re-reading multi-hundred-MB CSVs) keeps this cheap.

```bash
docker compose --env-file .env -f docker/docker-compose.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "<query>"
```

## Steps

1. **Scope.** List the target table(s): `\dt raw.*` or
   `SELECT table_name FROM information_schema.tables WHERE table_schema='raw'`.
2. **Per table:**
   - Row count.
   - Per column: null count and rate.
   - For low-cardinality / categorical columns: distinct count and a
     `GROUP BY ... ORDER BY count DESC` breakdown.
   - For numeric and date/timestamp columns: min, max, and a couple of
     percentiles if the range looks suspicious.
3. **Cross-table:** referential integrity between the table and whatever it's
   supposed to join to (orphan rows via `LEFT JOIN ... WHERE x.id IS NULL`).
4. **Report concisely** -- a markdown table of column-level stats plus a short
   list of flagged anomalies. Don't dump full result sets into the
   conversation; summarize.
5. If the profiling is in service of answering a specific business question
   rather than general characterization, hand the result to the
   `findings-writeup` skill instead of stopping at the raw numbers.

## Known Olist quirks worth checking for, not assuming

- `orders.order_status` has 8 values (`delivered`, `shipped`, `canceled`,
  `unavailable`, `invoiced`, `processing`, `created`, `approved`) --
  `delivered` is ~97% but not 100%; metrics touching revenue need an explicit
  status filter, not an assumption that every row is a completed sale.
- `order_reviews.review_id` is **not unique** -- some review_ids repeat across
  rows, and some orders have more than one review. Don't treat it as a primary
  key without checking.
- `order_payments` is one-to-many per order (installments/split payment
  methods); `COUNT(*)` over this table is not "number of orders."
- `geolocation` is zip-code-prefix grain with heavy duplication (far more rows
  than distinct prefixes) -- it's a noisy lookup, not a clean dimension table.
- `products.product_category_name` is in Portuguese and has nulls; join
  `product_category_name_translation` for English names, and decide
  explicitly how to handle the nulls rather than silently dropping rows.
- Timestamp columns were parsed to real `timestamp` columns at load time
  (not `text`) -- if a query needs a cast, that's a sign something's off.
