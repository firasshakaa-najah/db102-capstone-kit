-- DB-102 capstone: sanity checks after loading.
USE shopdb;

SELECT 'tenants'     AS tbl, COUNT(*) AS rows_ FROM tenants
UNION ALL SELECT 'stores',      COUNT(*) FROM stores
UNION ALL SELECT 'products',    COUNT(*) FROM products
UNION ALL SELECT 'customers',   COUNT(*) FROM customers
UNION ALL SELECT 'orders',      COUNT(*) FROM orders
UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
UNION ALL SELECT 'payments',    COUNT(*) FROM payments
UNION ALL SELECT 'events',      COUNT(*) FROM events;

-- The skew: a handful of stores own most of the orders (one big store = one hot shard, week 10)
SELECT s.id AS store_id, s.name, COUNT(*) AS orders_,
       ROUND(100 * COUNT(*) / (SELECT COUNT(*) FROM orders), 2) AS pct_of_all_orders
FROM orders o JOIN stores s ON s.id = o.store_id
GROUP BY s.id, s.name
ORDER BY orders_ DESC
LIMIT 10;

-- Time span and growth
SELECT MIN(created_at) AS first_order, MAX(created_at) AS last_order,
       COUNT(*) / DATEDIFF(MAX(created_at), MIN(created_at)) AS orders_per_day FROM orders;

-- Status mix
SELECT status, COUNT(*) AS n, ROUND(100 * COUNT(*) / (SELECT COUNT(*) FROM orders), 1) AS pct
FROM orders GROUP BY status ORDER BY n DESC;

-- Indexes you wrote vs indexes InnoDB added for you (look at orders and events)
SELECT table_name, index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS columns_, non_unique
FROM information_schema.statistics
WHERE table_schema = 'shopdb'
GROUP BY table_name, index_name, non_unique
ORDER BY table_name, index_name;

-- On-disk size per table (MB)
SELECT table_name, table_rows AS est_rows,
       ROUND((data_length + index_length) / 1024 / 1024) AS total_mb,
       ROUND(index_length / 1024 / 1024) AS index_mb
FROM information_schema.tables
WHERE table_schema = 'shopdb'
ORDER BY total_mb DESC;
