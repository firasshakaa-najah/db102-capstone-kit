# Instructor notes

Background for the instructor. Students do not need this page to use the kit, and the
"deliberately missing" list gives away what the week 3 lab asks them to find.

## The tenancy decision (instructor reference)

One shared schema, `tenant_id` on every tenant-scoped row. A **tenant** is a
merchant account; a tenant owns one or more **stores** (20 merchants own two).
`tenant_id` is denormalized onto `orders`, `order_items`, `payments` and `events`
even though it is derivable through `store_id`: every query, every index and,
in week 10, every shard key starts with "which tenant?". Students still argue
the three options (shared schema / schema-per-tenant / database-per-tenant) in
the week 2 lab; this is the one the reference platform runs on.

The skew is the point. Store 1 owns 12% of all orders, stores 2–4 about 5% each,
the tail is tiny. Traffic in the workload runner is proportional to store size,
so store 1 is also the hot store. Week 10's "one big store = one hot shard" is
already in the data.

## What is deliberately missing (do not fix this in week 2)

Week 3 needs slow queries to exist; week 4 fixes them.

* `orders`: no index on `order_number`, `status`, `created_at`, `shipping_city` — the
  support tool's lookup by order number is a full scan, and so is the daily revenue report.
* `events`: nothing but the primary key. No foreign keys either (it is an append-only
  log). Every clickstream query scans 44M rows.
* `payments`: no index on `status`; `customers`: none on `last_name`.
* The only secondary indexes on the big tables are the ones InnoDB *insists* on for
  foreign keys (`fk_orders_store`, `fk_orders_customer`, ...). Run the index query in
  `sql/verify.sql` and notice the indexes you never wrote — a week 4 talking point.
* Statistics: `load.sql` ends with `ANALYZE TABLE`, so the optimizer's estimates start
  honest. Drop that line once and watch what the plans do (week 3, "stale statistics").
