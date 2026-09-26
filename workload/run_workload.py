#!/usr/bin/env python3
"""
DB-102 workload runner: the "application" that hits the capstone database.

Runs a weighted mix of the queries a Shopify-like platform issues (storefront,
checkout, merchant dashboard, support tool, reports), from N threads, for a fixed
duration. Some of them are deliberately bad. Your job in week 3 is not to read
this file and spot them; it is to find them from the database's own evidence
(performance_schema, sys.statement_analysis, the slow log), then prove it with
EXPLAIN ANALYZE.

    python run_workload.py --threads 8 --duration 300
    python run_workload.py --heavy 0          # skip the catastrophic report queries (smoke test)
    python run_workload.py --list             # print the query mix and exit

    python run_workload.py --target full      # the scale-1 server (port 3307) instead of the lab one

Connection: --target lab|full picks the docker-compose server (lab = port 3306, full = port 3307);
--host/--port/--user/--password/--db override it (defaults match docker-compose.yml).
Every SELECT carries MAX_EXECUTION_TIME(20000) so one bad query cannot hang a thread
forever; a timeout is counted, not fatal (that is also how production apps do it).
"""
import argparse
import random
import statistics
import threading
import time
from collections import defaultdict

import pymysql

# The two servers in docker-compose.yml: lab = service "mysql" (scale 0.2), full = "mysql-full" (scale 1).
TARGET_PORTS = {"lab": 3306, "full": 3307}

NOW = "2026-09-13 00:00:00"      # the dataset ends at semester start; "today" for the app
CAP = "/*+ MAX_EXECUTION_TIME(20000) */"


# ----------------------------------------------------------------------------
# The query mix. (name, weight, heavy?, sql, params-builder)
# ----------------------------------------------------------------------------
def _days_ago(rng, lo, hi):
    d = rng.randint(lo, hi)
    return f"DATE_SUB('{NOW}', INTERVAL {d} DAY)"


QUERIES = [
    # --- storefront / checkout: point lookups by key ---
    ("order_by_id", 18, False,
     f"SELECT {CAP} id, order_number, status, total, currency, created_at FROM orders WHERE id = %s",
     lambda s, r: (s.rand_order_id(r),)),
    ("order_items_for_order", 14, False,
     f"SELECT {CAP} oi.id, oi.quantity, oi.unit_price, oi.line_total, p.title, p.sku "
     f"FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = %s",
     lambda s, r: (s.rand_order_id(r),)),
    ("payments_for_order", 8, False,
     f"SELECT {CAP} id, provider, status, amount, created_at FROM payments WHERE order_id = %s",
     lambda s, r: (s.rand_order_id(r),)),
    ("store_by_slug", 6, False,
     f"SELECT {CAP} id, name, currency FROM stores WHERE slug = %s",
     lambda s, r: (r.choice(s.slugs),)),
    ("product_by_sku", 10, False,
     f"SELECT {CAP} id, title, price, stock_qty FROM products WHERE store_id = %s AND sku = %s",
     lambda s, r: r.choice(s.skus)),
    ("customer_by_email", 8, False,
     f"SELECT {CAP} id, first_name, last_name, city FROM customers WHERE store_id = %s AND email = %s",
     lambda s, r: r.choice(s.emails)),
    ("customer_order_history", 7, False,
     f"SELECT {CAP} id, order_number, status, total, created_at FROM orders "
     f"WHERE customer_id = %s ORDER BY created_at DESC LIMIT 20",
     lambda s, r: (s.rand_customer_id(r),)),

    # --- support tool / merchant dashboard ---
    ("order_by_number", 6, False,
     f"SELECT {CAP} id, store_id, status, total, created_at FROM orders WHERE order_number = %s",
     lambda s, r: (r.choice(s.order_numbers),)),
    ("recent_orders_store", 9, False,
     f"SELECT {CAP} id, order_number, status, total, created_at FROM orders "
     f"WHERE store_id = %s ORDER BY created_at DESC LIMIT 20",
     lambda s, r: (s.rand_store(r),)),
    ("store_orders_by_status", 5, False,
     f"SELECT {CAP} id, order_number, total, created_at FROM orders "
     f"WHERE store_id = %s AND status = %s AND created_at >= DATE_SUB('{NOW}', INTERVAL %s DAY) "
     f"ORDER BY created_at DESC LIMIT 50",
     lambda s, r: (s.rand_store(r), r.choice(["paid", "shipped", "delivered", "pending"]), r.randint(1, 60))),
    ("order_search_by_city", 2, False,
     f"SELECT {CAP} id, order_number, total, created_at FROM orders "
     f"WHERE store_id = %s AND shipping_city = %s ORDER BY created_at DESC LIMIT 50",
     lambda s, r: (s.rand_store(r), r.choice(s.cities))),
    ("customer_search_lastname", 3, False,
     f"SELECT {CAP} id, email, first_name, last_name FROM customers "
     f"WHERE store_id = %s AND last_name LIKE %s LIMIT 20",
     lambda s, r: (s.rand_store(r), r.choice(s.lastnames)[:3] + "%")),
    ("store_dashboard_kpis", 4, False,
     f"SELECT {CAP} COUNT(*), SUM(total), AVG(total) FROM orders "
     f"WHERE store_id = %s AND created_at >= DATE_SUB('{NOW}', INTERVAL %s DAY)",
     lambda s, r: (s.rand_store(r), r.randint(1, 30))),
    ("top_products_store", 2, False,
     f"SELECT {CAP} p.id, p.title, SUM(oi.quantity) AS qty FROM orders o "
     f"JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id "
     f"WHERE o.store_id = %s AND o.created_at >= DATE_SUB('{NOW}', INTERVAL %s DAY) "
     f"GROUP BY p.id, p.title ORDER BY qty DESC LIMIT 10",
     lambda s, r: (s.rand_store(r), r.randint(7, 30))),

    # --- platform reports (run rarely, cost a lot) ---
    ("daily_revenue_platform", 1, True,
     f"SELECT {CAP} DATE(created_at) AS d, COUNT(*) AS orders_, SUM(total) AS revenue FROM orders "
     f"WHERE created_at >= DATE_SUB('{NOW}', INTERVAL %s DAY) "
     f"AND status IN ('paid','fulfilled','shipped','delivered') GROUP BY d ORDER BY d",
     lambda s, r: (r.randint(7, 30),)),
    ("refunds_report", 1, True,
     f"SELECT {CAP} store_id, COUNT(*) AS n, SUM(amount) AS refunded FROM payments "
     f"WHERE status = 'refunded' AND created_at >= DATE_SUB('{NOW}', INTERVAL %s DAY) "
     f"GROUP BY store_id ORDER BY refunded DESC LIMIT 20",
     lambda s, r: (r.randint(30, 90),)),

    # --- analytics on the clickstream (the ones that hurt) ---
    ("events_for_session", 3, True,
     f"SELECT {CAP} id, event_type, product_id, occurred_at FROM events "
     f"WHERE session_id = %s ORDER BY occurred_at",
     lambda s, r: (r.choice(s.sessions),)),
    ("events_by_type_store", 1, True,
     f"SELECT {CAP} event_type, COUNT(*) FROM events "
     f"WHERE store_id = %s AND occurred_at >= DATE_SUB('{NOW}', INTERVAL %s DAY) GROUP BY event_type",
     lambda s, r: (s.rand_store(r), r.randint(1, 14))),
    ("product_view_funnel", 1, True,
     f"SELECT {CAP} COUNT(DISTINCT session_id) FROM events "
     f"WHERE product_id = %s AND event_type = 'product_view'",
     lambda s, r: (s.rand_product_id(r),)),
    ("abandoned_checkouts", 0.5, True,
     f"SELECT {CAP} COUNT(*) FROM events e WHERE e.store_id = %s AND e.event_type = 'checkout_started' "
     f"AND e.occurred_at >= DATE_SUB('{NOW}', INTERVAL %s DAY) "
     f"AND NOT EXISTS (SELECT 1 FROM events x WHERE x.session_id = e.session_id AND x.event_type = 'order_placed')",
     lambda s, r: (s.rand_store(r), r.randint(1, 7))),

    # --- writes: the storefront never stops logging ---
    ("insert_event", 6, False,
     "INSERT INTO events (tenant_id, store_id, customer_id, session_id, event_type, product_id, order_id, "
     "device, country_code, properties, occurred_at) VALUES (%s, %s, NULL, %s, 'page_view', NULL, NULL, %s, %s, %s, %s)",
     lambda s, r: s.new_event(r)),
]


class Sampler:
    """Draws realistic parameters from the live data (traffic is proportional to store size)."""

    def __init__(self, conn, rng):
        cur = conn.cursor()
        cur.execute("SELECT id, tenant_id FROM stores")
        self.tenant_of_store = {sid: tid for sid, tid in cur.fetchall()}
        cur.execute("SELECT store_id, COUNT(*) FROM orders GROUP BY store_id")
        rows = cur.fetchall()
        self.store_ids = [sid for sid, _ in rows]
        self.store_weights = [n for _, n in rows]
        cur.execute("SELECT MAX(id) FROM orders");    self.max_order = cur.fetchone()[0]
        cur.execute("SELECT MAX(id) FROM customers"); self.max_customer = cur.fetchone()[0]
        cur.execute("SELECT MAX(id) FROM products");  self.max_product = cur.fetchone()[0]
        cur.execute("SELECT MAX(id) FROM events");    self.max_event = cur.fetchone()[0]

        def sample(table, cols, maxid, n=300):
            ids = [rng.randint(1, maxid) for _ in range(n)]
            cur.execute(f"SELECT {cols} FROM {table} WHERE id IN ({','.join(map(str, ids))})")
            return cur.fetchall()

        self.order_numbers = [r[0] for r in sample("orders", "order_number", self.max_order)]
        self.sessions = [r[0] for r in sample("events", "session_id", self.max_event)]
        cust = sample("customers", "store_id, email, last_name, city", self.max_customer)
        self.emails = [(r[0], r[1]) for r in cust]
        self.lastnames = [r[2] for r in cust]
        self.cities = [r[3] for r in cust if r[3]]
        self.skus = [(r[0], r[1]) for r in sample("products", "store_id, sku", self.max_product)]
        cur.execute("SELECT slug FROM stores")
        self.slugs = [r[0] for r in cur.fetchall()]
        cur.close()

    def rand_store(self, r):
        return r.choices(self.store_ids, weights=self.store_weights, k=1)[0]

    def rand_order_id(self, r):        # recent orders are hotter than old ones
        return max(1, int(self.max_order * (1 - r.random() ** 3)))

    def rand_customer_id(self, r):
        return r.randint(1, self.max_customer)

    def rand_product_id(self, r):
        return r.randint(1, self.max_product)

    def new_event(self, r):
        sid = self.rand_store(r)
        return (self.tenant_of_store[sid], sid, "%032x" % r.getrandbits(128),
                r.choice(["desktop", "mobile", "app"]), r.choice(["PS", "JO", "AE", "SA", "EG"]),
                r.choice(['{"ref": "google"}', '{"ref": "direct"}', None]),
                time.strftime("%Y-%m-%d %H:%M:%S"))


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat = defaultdict(list)
        self.err = defaultdict(int)
        self.timeouts = defaultdict(int)

    def record(self, name, ms):
        with self.lock:
            self.lat[name].append(ms)

    def error(self, name, timeout):
        with self.lock:
            (self.timeouts if timeout else self.err)[name] += 1


def worker(idx, args, sampler, stats, deadline, mix):
    r = random.Random(args.seed * 1000 + idx)
    conn = pymysql.connect(host=args.host, port=args.port, user=args.user, password=args.password,
                           database=args.db, autocommit=True)
    cur = conn.cursor()
    names = [q[0] for q in mix]
    weights = [q[1] for q in mix]
    by_name = {q[0]: q for q in mix}
    while time.time() < deadline:
        name = r.choices(names, weights=weights, k=1)[0]
        _, _, _, sql, build = by_name[name]
        params = build(sampler, r)
        t = time.perf_counter()
        try:
            cur.execute(sql, params)
            cur.fetchall()
            stats.record(name, (time.perf_counter() - t) * 1000)
        except pymysql.err.OperationalError as e:
            if e.args and e.args[0] in (3024, 1317):        # max execution time / query interrupted
                stats.error(name, timeout=True)
                stats.record(name, (time.perf_counter() - t) * 1000)
            else:
                stats.error(name, timeout=False)
                if args.verbose:
                    print(f"[t{idx}] {name}: {e}")
        except Exception as e:                               # noqa: BLE001
            stats.error(name, timeout=False)
            if args.verbose:
                print(f"[t{idx}] {name}: {e}")
    cur.close()
    conn.close()


def report(stats, elapsed):
    rows = []
    for name, lat in stats.lat.items():
        lat_sorted = sorted(lat)
        p95 = lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))]
        rows.append((name, len(lat), stats.timeouts[name], stats.err[name],
                     statistics.mean(lat), p95, max(lat), sum(lat) / 1000))
    rows.sort(key=lambda x: -x[7])
    total = sum(r[1] for r in rows)
    print(f"\n{'query':<28}{'execs':>8}{'t/o':>6}{'err':>5}{'avg ms':>10}{'p95 ms':>10}{'max ms':>10}{'total s':>10}")
    for r in rows:
        print(f"{r[0]:<28}{r[1]:>8}{r[2]:>6}{r[3]:>5}{r[4]:>10.1f}{r[5]:>10.1f}{r[6]:>10.0f}{r[7]:>10.1f}")
    print(f"\n{total} statements in {elapsed:.0f}s = {total / elapsed:.0f} statements/s "
          f"(client-side view; now compare with sys.statement_analysis)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--target", choices=sorted(TARGET_PORTS), default="lab",
                    help="which docker-compose server: lab (scale 0.2, port 3306) or full (scale 1, port 3307)")
    ap.add_argument("--port", type=int, default=None, help="overrides the port implied by --target")
    ap.add_argument("--user", default="app")
    ap.add_argument("--password", default="app")
    ap.add_argument("--db", default="shopdb")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--duration", type=int, default=120, help="seconds")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--heavy", type=float, default=1.0, help="weight multiplier for the report/analytics queries (0 = off)")
    ap.add_argument("--only", help="comma-separated query names to run (everything else off)")
    ap.add_argument("--list", action="store_true", help="print the query mix and exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.port is None:
        args.port = TARGET_PORTS[args.target]

    mix = [(n, w * (args.heavy if heavy else 1.0), heavy, sql, b) for n, w, heavy, sql, b in QUERIES]
    if args.only:
        keep = set(args.only.split(","))
        mix = [q for q in mix if q[0] in keep]
    mix = [q for q in mix if q[1] > 0]
    if args.list:
        tw = sum(q[1] for q in mix)
        for n, w, heavy, sql, _ in mix:
            print(f"{n:<28}{100 * w / tw:5.1f}%  {'heavy' if heavy else ''}\n    {sql}\n")
        return

    conn = pymysql.connect(host=args.host, port=args.port, user=args.user, password=args.password, database=args.db)
    print(f"target {args.target}: {args.host}:{args.port}/{args.db} - sampling parameters from the live data ...", flush=True)
    sampler = Sampler(conn, random.Random(args.seed))
    conn.close()
    print(f"{len(sampler.store_ids)} stores, {sampler.max_order:,} orders, {sampler.max_event:,} events. "
          f"Running {len(mix)} query shapes on {args.threads} threads for {args.duration}s ...", flush=True)

    stats = Stats()
    deadline = time.time() + args.duration
    threads = [threading.Thread(target=worker, args=(i, args, sampler, stats, deadline, mix), daemon=True)
               for i in range(args.threads)]
    t0 = time.time()
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(5)
            n = sum(len(v) for v in stats.lat.values())
            print(f"  {time.time() - t0:5.0f}s  {n:8,} statements", flush=True)
    except KeyboardInterrupt:
        print("stopping ...")
    report(stats, time.time() - t0)


if __name__ == "__main__":
    main()
