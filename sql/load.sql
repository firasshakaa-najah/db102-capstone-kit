-- DB-102 capstone: bulk load the generated CSVs (server-side LOAD DATA INFILE).
-- Run through scripts/load.sh, which points '/data/' at your CSV folder.
-- Inside Docker the ./data folder is mounted read-only at /data and
-- secure_file_priv=/data allows the server to read it.

USE shopdb;

-- Speed knobs for a trusted bulk load. The generator guarantees referential
-- integrity and uniqueness, so we skip the checks while loading. (Week 7 asks
-- what sql_log_bin=0 costs you; the honest answer is "the binlog for this load".)
SET SESSION sql_log_bin = 0;
SET SESSION foreign_key_checks = 0;
SET SESSION unique_checks = 0;

LOAD DATA INFILE '/data/tenants.csv' INTO TABLE tenants
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, name, plan, country_code, created_at);

LOAD DATA INFILE '/data/stores.csv' INTO TABLE stores
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, name, slug, currency, created_at);

LOAD DATA INFILE '/data/products.csv' INTO TABLE products
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, store_id, sku, title, category, price, stock_qty, is_active, created_at);

LOAD DATA INFILE '/data/customers.csv' INTO TABLE customers
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, store_id, email, first_name, last_name, @phone, @city, country_code, created_at)
  SET phone = NULLIF(@phone, ''), city = NULLIF(@city, '');

LOAD DATA INFILE '/data/orders.csv' INTO TABLE orders
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, store_id, customer_id, order_number, status, channel, currency,
   subtotal, shipping_fee, discount, total, item_count, @shipping_city, shipping_country,
   created_at, updated_at)
  SET shipping_city = NULLIF(@shipping_city, '');

LOAD DATA INFILE '/data/order_items.csv' INTO TABLE order_items
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, order_id, product_id, quantity, unit_price, line_total);

LOAD DATA INFILE '/data/payments.csv' INTO TABLE payments
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, order_id, provider, status, amount, currency, @provider_ref, created_at)
  SET provider_ref = NULLIF(@provider_ref, '');

LOAD DATA INFILE '/data/events.csv' INTO TABLE events
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '\\'
  LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, tenant_id, store_id, @customer_id, session_id, event_type, @product_id, @order_id,
   device, country_code, @properties, occurred_at)
  SET customer_id = NULLIF(@customer_id, ''),
      product_id  = NULLIF(@product_id, ''),
      order_id    = NULLIF(@order_id, ''),
      properties  = NULLIF(@properties, '');

SET SESSION foreign_key_checks = 1;
SET SESSION unique_checks = 1;

-- Fresh statistics for the optimizer (week 3, slide "where the estimates come from").
ANALYZE TABLE tenants, stores, products, customers, orders, order_items, payments, events;
