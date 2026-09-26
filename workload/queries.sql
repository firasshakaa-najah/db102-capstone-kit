-- DB-102 week 3: the workload's query shapes with literal parameters, for reading
-- plans by hand. Store 1 is always the biggest store; order 1, customer 1 and
-- product 1 always exist; 'S001-00000001' is store 1's first order number.
-- Prefix any of them with EXPLAIN, EXPLAIN FORMAT=TREE or EXPLAIN ANALYZE.
USE shopdb;

-- pick some real parameter values first
SELECT session_id FROM events WHERE id = 12345;
SELECT store_id, email, last_name, city FROM customers WHERE id = 777;
SELECT store_id, sku FROM products WHERE id = 42;

-- storefront / checkout
SELECT id, order_number, status, total, currency, created_at FROM orders WHERE id = 1;
SELECT oi.id, oi.quantity, oi.unit_price, oi.line_total, p.title, p.sku
FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = 1;
SELECT id, provider, status, amount, created_at FROM payments WHERE order_id = 1;
SELECT id, name, currency FROM stores WHERE slug = (SELECT slug FROM stores WHERE id = 1);
SELECT id, title, price, stock_qty FROM products WHERE store_id = 1 AND sku = 'SKU-001-00001';
SELECT id, order_number, status, total, created_at FROM orders
WHERE customer_id = 1 ORDER BY created_at DESC LIMIT 20;

-- support tool / merchant dashboard
SELECT id, store_id, status, total, created_at FROM orders WHERE order_number = 'S001-00000001';
SELECT id, order_number, status, total, created_at FROM orders
WHERE store_id = 1 ORDER BY created_at DESC LIMIT 20;
SELECT id, order_number, total, created_at FROM orders
WHERE store_id = 1 AND status = 'paid' AND created_at >= DATE_SUB('2026-09-13', INTERVAL 30 DAY)
ORDER BY created_at DESC LIMIT 50;
SELECT id, email, first_name, last_name FROM customers
WHERE store_id = 1 AND last_name LIKE 'Smi%' LIMIT 20;
SELECT COUNT(*), SUM(total), AVG(total) FROM orders
WHERE store_id = 1 AND created_at >= DATE_SUB('2026-09-13', INTERVAL 30 DAY);
SELECT p.id, p.title, SUM(oi.quantity) AS qty
FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id
WHERE o.store_id = 1 AND o.created_at >= DATE_SUB('2026-09-13', INTERVAL 30 DAY)
GROUP BY p.id, p.title ORDER BY qty DESC LIMIT 10;

-- platform reports
SELECT DATE(created_at) AS d, COUNT(*) AS orders_, SUM(total) AS revenue FROM orders
WHERE created_at >= DATE_SUB('2026-09-13', INTERVAL 30 DAY)
  AND status IN ('paid','fulfilled','shipped','delivered')
GROUP BY d ORDER BY d;
SELECT store_id, COUNT(*) AS n, SUM(amount) AS refunded FROM payments
WHERE status = 'refunded' AND created_at >= DATE_SUB('2026-09-13', INTERVAL 90 DAY)
GROUP BY store_id ORDER BY refunded DESC LIMIT 20;

-- clickstream analytics (replace the session id with a real one from above)
SELECT id, event_type, product_id, occurred_at FROM events
WHERE session_id = 'REPLACE-ME' ORDER BY occurred_at;
SELECT event_type, COUNT(*) FROM events
WHERE store_id = 1 AND occurred_at >= DATE_SUB('2026-09-13', INTERVAL 7 DAY) GROUP BY event_type;
SELECT COUNT(DISTINCT session_id) FROM events WHERE product_id = 1 AND event_type = 'product_view';
SELECT COUNT(*) FROM events e
WHERE e.store_id = 1 AND e.event_type = 'checkout_started'
  AND e.occurred_at >= DATE_SUB('2026-09-13', INTERVAL 7 DAY)
  AND NOT EXISTS (SELECT 1 FROM events x WHERE x.session_id = e.session_id AND x.event_type = 'order_placed');
