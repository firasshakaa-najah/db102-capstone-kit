#!/usr/bin/env python3
"""
DB-102 capstone data generator: a multi-tenant e-commerce platform (Shopify-like).

Writes CSV files for `LOAD DATA INFILE` (see sql/load.sql). Deterministic for a
given --seed, so every team gets the same data and the same "ten worst queries".

    python generate.py --scale 0.05                 # ~2.5M rows, well under a minute
    python generate.py --scale 1 --out ../data      # the course dataset: 50M orders+events

Row plan at scale 1.0:  200 stores (~180 tenants), 60k products, 1.5M customers,
6M orders, ~14M order items, ~6M payments, 44M events  -> ~78M rows, ~8 GB of CSV.
Store sizes are deliberately skewed (log-normal, biggest store capped at 12% of all
orders): one big store = one hot shard, which weeks 3, 4 and 10 all depend on.
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from faker import Faker

# ----------------------------------------------------------------------------
# Row plan
# ----------------------------------------------------------------------------
BASE = dict(tenants=180, stores=200, products=60_000, customers=1_500_000,
            orders=6_000_000, events=44_000_000)
LINKED_EVENTS_PER_ORDER = 5          # product_view x2, add_to_cart, checkout_started, order_placed

T0 = np.datetime64("2024-09-01T00:00:00")   # first order
T1 = np.datetime64("2026-09-13T00:00:00")   # semester start: nothing after this
T0S = int(T0.astype("datetime64[s]").astype(np.int64))
T1S = int(T1.astype("datetime64[s]").astype(np.int64))
DAYS = (T1S - T0S) // 86400

COUNTRIES = ["PS", "JO", "AE", "SA", "EG", "TR", "DE", "GB", "US", "FR", "NL", "IT", "ES", "IN", "MA"]
COUNTRY_W = np.array([18, 14, 12, 10, 9, 6, 6, 5, 5, 3, 3, 3, 2, 2, 2], dtype=float)
COUNTRY_W /= COUNTRY_W.sum()
CURRENCY = {"PS": "ILS", "JO": "JOD", "AE": "AED", "SA": "SAR", "EG": "EGP", "TR": "TRY",
            "DE": "EUR", "FR": "EUR", "NL": "EUR", "IT": "EUR", "ES": "EUR",
            "GB": "GBP", "US": "USD", "IN": "INR", "MA": "MAD"}

CATEGORIES = {
    "Apparel":      ["T-Shirt", "Hoodie", "Jeans", "Jacket", "Dress", "Abaya", "Scarf", "Socks"],
    "Footwear":     ["Sneakers", "Boots", "Sandals", "Loafers", "Running Shoes", "Slippers"],
    "Electronics":  ["Earbuds", "Power Bank", "Phone Case", "Charger", "Smart Watch", "Speaker"],
    "Home":         ["Mug", "Candle", "Cushion", "Blanket", "Vase", "Lamp", "Rug"],
    "Beauty":       ["Serum", "Moisturizer", "Perfume", "Lipstick", "Hair Oil", "Face Mask"],
    "Grocery":      ["Olive Oil", "Za'atar", "Dates", "Coffee Beans", "Honey", "Tea", "Halva"],
    "Sports":       ["Yoga Mat", "Water Bottle", "Dumbbells", "Football", "Gym Bag"],
    "Toys":         ["Puzzle", "Plush Bear", "Building Blocks", "Board Game", "Kite"],
    "Books":        ["Notebook", "Planner", "Novel", "Cookbook", "Sketchbook"],
    "Jewelry":      ["Necklace", "Bracelet", "Earrings", "Ring", "Watch"],
    "Baby":         ["Onesie", "Stroller", "Bib Set", "Rattle", "Baby Blanket"],
    "Garden":       ["Planter", "Seeds Pack", "Garden Gloves", "Watering Can"],
}
ADJECTIVES = ["Classic", "Premium", "Eco", "Vintage", "Modern", "Handmade", "Organic", "Slim",
              "Deluxe", "Essential", "Signature", "Compact", "Travel", "Everyday", "Limited"]
MATERIALS = ["Cotton", "Linen", "Leather", "Wool", "Bamboo", "Ceramic", "Steel", "Silk",
             "Denim", "Canvas", "Glass", "Oak", "Recycled", "Silver", "Brass"]
SEARCH_TERMS = ["shoes", "hoodie", "gift", "sale", "olive oil", "watch", "dress", "kids",
                "black", "summer", "winter jacket", "coffee", "earbuds", "candle", "ring",
                "backpack", "socks", "perfume", "yoga", "lamp", "mug", "jeans", "abaya",
                "dates", "planner", "sneakers", "scarf", "serum", "blanket", "puzzle"]
REFERRERS = ["google", "direct", "instagram", "facebook", "email", "tiktok", "whatsapp"]

STATUS_BASE = (["pending", "paid", "fulfilled", "shipped", "delivered", "cancelled", "refunded"],
               [0.02, 0.10, 0.08, 0.14, 0.58, 0.05, 0.03])
STATUS_FRESH = (["pending", "paid", "fulfilled"], [0.45, 0.40, 0.15])      # < 3 days old
STATUS_RECENT = (["paid", "fulfilled", "shipped"], [0.25, 0.30, 0.45])     # < 12 days old
CHANNELS = (["web", "mobile", "pos", "api"], [0.55, 0.35, 0.06, 0.04])
DEVICES = (["desktop", "mobile", "tablet", "app"], [0.32, 0.45, 0.06, 0.17])
PROVIDERS = (["card", "paypal", "cod", "bank_transfer", "wallet"], [0.58, 0.14, 0.18, 0.04, 0.06])
BROWSE_TYPES = (["page_view", "product_view", "search", "add_to_cart", "remove_from_cart", "checkout_started"],
                [0.44, 0.31, 0.12, 0.08, 0.02, 0.03])

CSV_KW = dict(index=False, na_rep="", doublequote=False, escapechar="\\", lineterminator="\n")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def choice(rng, spec, n):
    values, probs = spec
    return rng.choice(np.array(values, dtype=object), size=n, p=np.array(probs) / np.sum(probs))


def hex32(rng, n):
    """n random 32-char hex ids, vectorized."""
    a = rng.integers(0, 2**63 - 1, size=n, dtype=np.int64)
    b = rng.integers(0, 2**63 - 1, size=n, dtype=np.int64)
    return np.char.add(np.char.mod("%016x", a), np.char.mod("%016x", b))


def day_weights():
    days = np.arange(DAYS)
    growth = 1.0 + 1.2 * days / DAYS                      # platform grows ~2.2x over two years
    dow = ((T0.astype("datetime64[D]").astype(np.int64) + days + 3) % 7)   # 1970-01-01 was a Thursday -> Mon=0
    weekly = np.array([1.0, 0.98, 1.0, 1.05, 1.22, 1.35, 1.2])[dow]
    dates = T0.astype("datetime64[D]") + days
    month = dates.astype("datetime64[M]").astype(int) % 12 + 1
    dom = (dates - dates.astype("datetime64[M]")).astype(int) + 1
    season = np.where((month == 11) & (dom >= 20), 1.9, 1.0)                 # Black Friday week
    season = np.where(month == 12, season * 1.25, season)
    season = np.where(month == 3, season * 1.15, season)                      # Ramadan-ish bump
    w = growth * weekly * season
    return w / w.sum()


HOUR_W = np.array([1, 0.6, 0.4, 0.3, 0.3, 0.4, 0.7, 1.2, 1.8, 2.3, 2.6, 2.8, 3.0, 2.9, 2.7, 2.6,
                   2.7, 3.0, 3.4, 3.8, 4.2, 4.0, 3.2, 2.0], dtype=float)
HOUR_W /= HOUR_W.sum()
DAY_W = day_weights()


def sample_times(rng, n):
    """Epoch seconds with growth, weekly and seasonal shape (int64)."""
    day = rng.choice(DAYS, size=n, p=DAY_W)
    hour = rng.choice(24, size=n, p=HOUR_W)
    sec = rng.integers(0, 3600, size=n)
    return T0S + day * 86400 + hour * 3600 + sec


def to_dt(seconds):
    return pd.to_datetime(seconds.astype("int64"), unit="s")


def to_dt_ms(ms):
    return pd.to_datetime(ms.astype("int64"), unit="ms")


def money(x):
    return np.round(x, 2)


def zpad(arr, width):
    return np.char.zfill(arr.astype(np.int64).astype(str), width)


class Writer:
    """Appends chunks to one CSV per table; writes the header once."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.started = set()

    def write(self, table, df):
        path = os.path.join(self.out_dir, f"{table}.csv")
        first = table not in self.started
        df.to_csv(path, mode="w" if first else "a", header=first, **CSV_KW)
        self.started.add(table)


# ----------------------------------------------------------------------------
# Dimension tables
# ----------------------------------------------------------------------------
def build_dimensions(rng, fake, n_products, n_customers):
    S = BASE["stores"]
    T = BASE["tenants"]

    # store weights: log-normal, biggest store capped at 12% of all orders
    w = rng.lognormal(mean=0.0, sigma=1.5, size=S)
    for _ in range(50):
        w = w / w.sum()
        cap = 0.12
        if w.max() <= cap + 1e-9:
            break
        excess = (w - cap).clip(min=0).sum()
        w = np.minimum(w, cap)
        under = w < cap
        w[under] += excess * w[under] / w[under].sum()
    w = w / w.sum()
    order_share = np.sort(w)[::-1]        # store 1 is the biggest, then descending: easy to reason about in lab
    store_w = order_share

    # tenants: 180 tenants; the 20 extra stores go to the 20 biggest tenants (multi-store merchants)
    tenant_of_store = np.arange(S)
    tenant_of_store[T:] = np.arange(S - T)            # stores 181..200 belong to tenants 1..20
    tenant_country = rng.choice(len(COUNTRIES), size=T, p=COUNTRY_W)
    tenant_weight = np.bincount(tenant_of_store, weights=store_w, minlength=T)
    rank = np.argsort(-tenant_weight)
    plan = np.full(T, "basic", dtype=object)
    plan[rank[: int(T * 0.10)]] = "enterprise"
    plan[rank[int(T * 0.10): int(T * 0.40)]] = "pro"
    company_names = []
    seen = set()
    while len(company_names) < T:
        c = fake.company().replace(",", "")
        if c not in seen:
            seen.add(c)
            company_names.append(c)
    tenants = pd.DataFrame({
        "id": np.arange(1, T + 1),
        "name": company_names,
        "plan": plan,
        "country_code": np.array(COUNTRIES)[tenant_country],
        "created_at": to_dt(T0S - rng.integers(30, 900, size=T) * 86400),
    })

    suffix = np.array(["Store", "Shop", "Online", "Outlet", "Market"])[rng.integers(0, 5, size=S)]
    suffix[T:] = np.array(["Outlet", "EU Shop", "Wholesale", "Kids"])[rng.integers(0, 4, size=S - T)]
    store_names = [f"{company_names[t]} {sfx}" for t, sfx in zip(tenant_of_store, suffix)]
    slugs = ["-".join("".join(ch if ch.isalnum() or ch == " " else " " for ch in n.lower()).split()) + f"-{i + 1}"
             for i, n in enumerate(store_names)]
    store_country = tenant_country[tenant_of_store]
    stores = pd.DataFrame({
        "id": np.arange(1, S + 1),
        "tenant_id": tenant_of_store + 1,
        "name": store_names,
        "slug": slugs,
        "currency": [CURRENCY[COUNTRIES[c]] for c in store_country],
        "created_at": pd.DatetimeIndex(tenants["created_at"].to_numpy()[tenant_of_store]) + pd.to_timedelta(rng.integers(0, 60, size=S), unit="D"),
    })

    # products per store: proportional to weight^0.6 with a floor
    p_alloc = np.maximum(12, np.round(n_products * (store_w ** 0.6) / (store_w ** 0.6).sum())).astype(int)
    prod_store = np.repeat(np.arange(S), p_alloc)
    P = len(prod_store)
    within = np.concatenate([np.arange(k) for k in p_alloc])
    cat_names = np.array(list(CATEGORIES.keys()), dtype=object)
    cat_idx = rng.integers(0, len(cat_names), size=P)
    nouns = [rng.choice(CATEGORIES[cat_names[c]]) for c in cat_idx]
    titles = [f"{a} {m} {n}" for a, m, n in zip(rng.choice(ADJECTIVES, size=P), rng.choice(MATERIALS, size=P), nouns)]
    price = money(np.clip(rng.lognormal(mean=3.3, sigma=0.8, size=P), 2.5, 2500))
    products = pd.DataFrame({
        "id": np.arange(1, P + 1),
        "tenant_id": tenant_of_store[prod_store] + 1,
        "store_id": prod_store + 1,
        "sku": np.char.add(np.char.add("SKU-", zpad(prod_store + 1, 3)), np.char.add("-", zpad(within + 1, 5))),
        "title": titles,
        "category": cat_names[cat_idx],
        "price": price,
        "stock_qty": np.where(rng.random(P) < 0.08, 0, rng.integers(1, 800, size=P)),
        "is_active": (rng.random(P) < 0.95).astype(int),
        "created_at": to_dt(T0S - rng.integers(0, 700, size=P) * 86400),
    })

    # customers per store: proportional to order share with a floor
    c_alloc = np.maximum(25, np.round(n_customers * store_w)).astype(int)
    cust_store = np.repeat(np.arange(S), c_alloc)
    C = len(cust_store)
    first = np.array([fake.first_name() for _ in range(400)], dtype=object)
    last = np.array([fake.last_name() for _ in range(400)], dtype=object)
    cities = np.array(sorted({fake.city() for _ in range(600)}), dtype=object)
    domains = np.array(["gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me"], dtype=object)
    fi = rng.integers(0, len(first), size=C)
    li = rng.integers(0, len(last), size=C)
    city_idx = rng.integers(0, len(cities), size=C)
    # customers mostly share the store's country
    other = rng.choice(len(COUNTRIES), size=C, p=COUNTRY_W)
    cust_country = np.where(rng.random(C) < 0.8, store_country[cust_store], other)
    within_c = np.concatenate([np.arange(k) for k in c_alloc])
    clean = lambda arr: np.char.replace(np.char.replace(np.char.lower(arr.astype(str)), "'", ""), " ", "")
    email = np.char.add(
        np.char.add(np.char.add(clean(first[fi]), "."), clean(last[li])),
        np.char.add(np.char.add(within_c.astype(str), "@"), domains[rng.integers(0, len(domains), size=C)].astype(str)),
    )
    phone = np.where(rng.random(C) < 0.7,
                     np.char.add("+9705", zpad(rng.integers(0, 10**8, size=C), 8)), "")
    customers = pd.DataFrame({
        "id": np.arange(1, C + 1),
        "tenant_id": tenant_of_store[cust_store] + 1,
        "store_id": cust_store + 1,
        "email": email,
        "first_name": first[fi],
        "last_name": last[li],
        "phone": phone,
        "city": cities[city_idx],
        "country_code": np.array(COUNTRIES)[cust_country],
        "created_at": to_dt(T0S - rng.integers(1, 400, size=C) * 86400 + rng.integers(0, 86400, size=C)),
    })

    dims = dict(
        S=S, T=T, store_w=store_w, tenant_of_store=tenant_of_store, store_country=store_country,
        store_currency=np.array(stores["currency"], dtype=object),
        prod_count=p_alloc, prod_offset=np.concatenate([[0], np.cumsum(p_alloc)[:-1]]), prod_price=price,
        cust_count=c_alloc, cust_offset=np.concatenate([[0], np.cumsum(c_alloc)[:-1]]),
        cust_city=cities[city_idx], cust_country=np.array(COUNTRIES)[cust_country],
    )
    return tenants, stores, products, customers, dims


# ----------------------------------------------------------------------------
# Fact tables, chunk by chunk
# ----------------------------------------------------------------------------
def generate_facts(rng, dims, writer, n_orders, n_events, chunk_size):
    S = dims["S"]
    log(f"orders: sampling {n_orders:,} timestamps and stores")
    store_all = rng.choice(S, size=n_orders, p=dims["store_w"]).astype(np.int32)
    t_all = sample_times(rng, n_orders)
    order = np.argsort(t_all, kind="stable")
    store_all, t_all = store_all[order], t_all[order]
    del order

    browse_per_order = max(0.0, n_events / n_orders - LINKED_EVENTS_PER_ORDER)
    store_seq = np.zeros(S, dtype=np.int64)          # per-store running order number
    next_item = 1
    next_payment = 1
    next_event = 1
    counts = dict(orders=0, order_items=0, payments=0, events=0)

    tenant_of_store = dims["tenant_of_store"]
    currency = dims["store_currency"]

    for a in range(0, n_orders, chunk_size):
        b = min(a + chunk_size, n_orders)
        m = b - a
        ids = np.arange(a + 1, b + 1, dtype=np.int64)
        store = store_all[a:b]
        t = t_all[a:b]
        tenant = tenant_of_store[store] + 1

        # customers: repeat buyers are the low indices (u^2 skew)
        nc = dims["cust_count"][store]
        cidx = np.floor(nc * rng.random(m) ** 2).astype(np.int64)
        customer_id = dims["cust_offset"][store] + cidx + 1

        # per-store sequential order numbers, e.g. S042-00001234
        seq_in_chunk = pd.Series(store).groupby(store).cumcount().to_numpy()
        seq = store_seq[store] + seq_in_chunk + 1
        store_seq += np.bincount(store, minlength=S)
        order_number = np.char.add(np.char.add("S", zpad(store + 1, 3)), np.char.add("-", zpad(seq, 8)))

        # status depends on age
        age_days = (T1S - t) / 86400.0
        st_base = choice(rng, STATUS_BASE, m)
        st_fresh = choice(rng, STATUS_FRESH, m)
        st_recent = choice(rng, STATUS_RECENT, m)
        status = np.where(age_days < 3, st_fresh, np.where(age_days < 12, st_recent, st_base))
        channel = choice(rng, CHANNELS, m)

        # ---- order items ----
        item_count = np.clip(1 + rng.poisson(1.4, size=m), 1, 8)
        total_items = int(item_count.sum())
        oi_order_idx = np.repeat(np.arange(m), item_count)
        oi_store = store[oi_order_idx]
        npd = dims["prod_count"][oi_store]
        pidx = np.floor(npd * rng.random(total_items) ** 2.5).astype(np.int64)
        product_id = dims["prod_offset"][oi_store] + pidx + 1
        qty = rng.choice([1, 2, 3, 4], size=total_items, p=[0.72, 0.18, 0.07, 0.03])
        unit_price = dims["prod_price"][product_id - 1]
        line_total = money(qty * unit_price)
        items = pd.DataFrame({
            "id": np.arange(next_item, next_item + total_items, dtype=np.int64),
            "tenant_id": tenant[oi_order_idx],
            "order_id": ids[oi_order_idx],
            "product_id": product_id,
            "quantity": qty,
            "unit_price": unit_price,
            "line_total": line_total,
        })
        next_item += total_items

        subtotal = money(np.bincount(oi_order_idx, weights=line_total, minlength=m))
        shipping = rng.choice([0.0, 4.99, 9.99, 14.99], size=m, p=[0.35, 0.30, 0.25, 0.10])
        discount = np.where(rng.random(m) < 0.15, money(subtotal * rng.uniform(0.05, 0.30, size=m)), 0.0)
        total = money(np.maximum(subtotal + shipping - discount, 0.0))
        updated = t + rng.integers(60, 7 * 86400, size=m)
        orders = pd.DataFrame({
            "id": ids,
            "tenant_id": tenant,
            "store_id": store + 1,
            "customer_id": customer_id,
            "order_number": order_number,
            "status": status,
            "channel": channel,
            "currency": currency[store],
            "subtotal": subtotal,
            "shipping_fee": shipping,
            "discount": discount,
            "total": total,
            "item_count": item_count,
            "shipping_city": dims["cust_city"][customer_id - 1],
            "shipping_country": dims["cust_country"][customer_id - 1],
            "created_at": to_dt(t),
            "updated_at": to_dt(updated),
        })

        # ---- payments ----
        paid_like = np.isin(status, ["paid", "fulfilled", "shipped", "delivered", "refunded"])
        pending_auth = (status == "pending") & (rng.random(m) < 0.6)
        failed = (status == "cancelled") & (rng.random(m) < 0.5)
        has_pay = paid_like | pending_auth | failed
        p_idx = np.nonzero(has_pay)[0]
        p_status = np.where(paid_like[p_idx], "captured", np.where(failed[p_idx], "failed", "authorized"))
        refund_idx = np.nonzero(status == "refunded")[0]
        all_idx = np.concatenate([p_idx, refund_idx])
        all_status = np.concatenate([p_status, np.full(len(refund_idx), "refunded", dtype=object)])
        p_time = np.concatenate([t[p_idx] + rng.integers(5, 900, size=len(p_idx)),
                                 t[refund_idx] + rng.integers(2 * 86400, 20 * 86400, size=len(refund_idx))])
        n_pay = len(all_idx)
        payments = pd.DataFrame({
            "id": np.arange(next_payment, next_payment + n_pay, dtype=np.int64),
            "tenant_id": tenant[all_idx],
            "order_id": ids[all_idx],
            "provider": choice(rng, PROVIDERS, n_pay),
            "status": all_status,
            "amount": total[all_idx],
            "currency": currency[store[all_idx]],
            "provider_ref": np.char.add("PAY-", np.char.upper(hex32(rng, n_pay).astype(str))).astype(object),
            "created_at": to_dt(p_time),
        })
        payments["provider_ref"] = payments["provider_ref"].str.slice(0, 20)
        next_payment += n_pay

        # ---- events linked to orders: 5 per order, same session ----
        session = hex32(rng, m)
        first_item = np.searchsorted(oi_order_idx, np.arange(m))            # index of each order's first item
        last_item = first_item + item_count - 1
        device = np.where(np.isin(channel, ["mobile"]), choice(rng, (["mobile", "app"], [0.6, 0.4]), m),
                          np.where(channel == "web", choice(rng, (["desktop", "tablet", "mobile"], [0.7, 0.1, 0.2]), m), "app"))
        cust_country = dims["cust_country"][customer_id - 1]
        linked = []
        # seconds before the order, non-overlapping so the session reads in the right order
        for etype, lo, hi, prod in [("product_view", 1500, 900, first_item),
                                    ("product_view", 899, 480, last_item),
                                    ("add_cart", 479, 150, first_item),
                                    ("checkout", 149, 20, None),
                                    ("placed", 0, 0, None)]:
            et = {"add_cart": "add_to_cart", "checkout": "checkout_started", "placed": "order_placed"}.get(etype, etype)
            offset = 0 if lo == 0 else rng.integers(hi, lo, size=m)
            linked.append(pd.DataFrame({
                "tenant_id": tenant,
                "store_id": store + 1,
                "customer_id": customer_id,
                "session_id": session,
                "event_type": et,
                "product_id": product_id[prod] if prod is not None else np.full(m, np.nan),
                "order_id": ids if etype == "placed" else np.full(m, np.nan),
                "device": device,
                "country_code": cust_country,
                "properties": np.full(m, "", dtype=object),
                "occurred_at_ms": (t - offset) * 1000 + rng.integers(0, 1000, size=m),
            }))

        # ---- browse-only sessions (never convert) ----
        n_browse = int(round(browse_per_order * m))
        browse = None
        if n_browse > 0:
            n_sess = max(1, int(n_browse / 3.0))
            per_sess = 1 + rng.poisson(2.0, size=n_sess)
            sess_idx = np.repeat(np.arange(n_sess), per_sess)[:n_browse]
            if len(sess_idx) < n_browse:                       # pad with 1-event sessions
                extra = np.arange(n_sess, n_sess + (n_browse - len(sess_idx)))
                sess_idx = np.concatenate([sess_idx, extra])
                n_sess = n_sess + len(extra)
            b_store = rng.choice(S, size=n_sess, p=dims["store_w"])[sess_idx]
            t_lo, t_hi = int(t.min()), int(t.max()) + 1
            base_t = rng.integers(t_lo, t_hi, size=n_sess)[sess_idx]
            step = rng.integers(5, 240, size=n_browse)
            within_sess = pd.Series(sess_idx).groupby(sess_idx).cumcount().to_numpy()
            occurred = base_t + within_sess * step
            b_type = choice(rng, BROWSE_TYPES, n_browse)
            known = rng.random(n_browse) < 0.25
            nc_b = dims["cust_count"][b_store]
            b_cust = dims["cust_offset"][b_store] + np.floor(nc_b * rng.random(n_browse) ** 2).astype(np.int64) + 1
            npd_b = dims["prod_count"][b_store]
            b_prod = dims["prod_offset"][b_store] + np.floor(npd_b * rng.random(n_browse) ** 2.5).astype(np.int64) + 1
            needs_prod = np.isin(b_type, ["product_view", "add_to_cart", "remove_from_cart"])
            props = np.full(n_browse, "", dtype=object)
            is_search = b_type == "search"
            props[is_search] = [f'{{"q": "{q}"}}' for q in rng.choice(SEARCH_TERMS, size=int(is_search.sum()))]
            is_pv = (b_type == "page_view") & (rng.random(n_browse) < 0.3)
            props[is_pv] = [f'{{"ref": "{r}"}}' for r in rng.choice(REFERRERS, size=int(is_pv.sum()))]
            browse = pd.DataFrame({
                "tenant_id": tenant_of_store[b_store] + 1,
                "store_id": b_store + 1,
                "customer_id": np.where(known, b_cust, np.nan),
                "session_id": hex32(rng, n_sess)[sess_idx],
                "event_type": b_type,
                "product_id": np.where(needs_prod, b_prod, np.nan),
                "order_id": np.full(n_browse, np.nan),
                "device": choice(rng, DEVICES, n_browse),
                "country_code": np.array(COUNTRIES)[np.where(rng.random(n_browse) < 0.8, dims["store_country"][b_store],
                                                              rng.choice(len(COUNTRIES), size=n_browse, p=COUNTRY_W))],
                "properties": props,
                "occurred_at_ms": occurred * 1000 + rng.integers(0, 1000, size=n_browse),
            })

        events = pd.concat(linked + ([browse] if browse is not None else []), ignore_index=True)
        events.sort_values("occurred_at_ms", kind="stable", inplace=True, ignore_index=True)
        n_ev = len(events)
        events.insert(0, "id", np.arange(next_event, next_event + n_ev, dtype=np.int64))
        events["occurred_at"] = to_dt_ms(events.pop("occurred_at_ms").to_numpy())
        for col in ("customer_id", "product_id", "order_id"):
            events[col] = events[col].astype("Int64")            # nullable ints -> empty string in CSV
        next_event += n_ev

        writer.write("orders", orders)
        writer.write("order_items", items)
        writer.write("payments", payments)
        writer.write("events", events)
        counts["orders"] += m
        counts["order_items"] += total_items
        counts["payments"] += n_pay
        counts["events"] += n_ev
        log(f"  orders {b:,}/{n_orders:,}  items {counts['order_items']:,}  payments {counts['payments']:,}  events {counts['events']:,}")

    return counts


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=0.05, help="fraction of the full dataset (1.0 = the course dataset)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--chunk", type=int, default=250_000, help="orders per chunk (memory vs speed)")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    s = args.scale
    n_orders = max(2_000, int(BASE["orders"] * s))
    n_events = max(int(n_orders * LINKED_EVENTS_PER_ORDER), int(BASE["events"] * s))
    n_products = max(BASE["stores"] * 15, int(BASE["products"] * s))
    n_customers = max(BASE["stores"] * 25, int(BASE["customers"] * s))
    est_gb = (n_orders * 210 + n_orders * 2.4 * 55 + n_orders * 1.03 * 95 + n_events * 125 + n_customers * 130 + n_products * 110) / 1e9
    log(f"scale {s}: ~{n_orders:,} orders, ~{int(n_orders * 2.4):,} items, ~{n_events:,} events, "
        f"{n_customers:,} customers, {n_products:,} products -> about {est_gb:.1f} GB of CSV in {out}")

    rng = np.random.default_rng(args.seed)
    fake = Faker("en_US")
    Faker.seed(args.seed)

    t_start = time.time()
    writer = Writer(out)
    tenants, stores, products, customers, dims = build_dimensions(rng, fake, n_products, n_customers)
    for name, df in [("tenants", tenants), ("stores", stores), ("products", products), ("customers", customers)]:
        writer.write(name, df)
        log(f"{name}: {len(df):,} rows")
    counts = generate_facts(rng, dims, writer, n_orders, n_events, min(args.chunk, n_orders))

    top = np.argsort(-dims["store_w"])[:5]
    manifest = dict(
        scale=s, seed=args.seed, generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        rows=dict(tenants=len(tenants), stores=len(stores), products=len(products), customers=len(customers), **counts),
        biggest_stores=[dict(store_id=int(i + 1), order_share=round(float(dims["store_w"][i]), 4)) for i in top],
        time_range=[str(T0), str(T1)],
    )
    with open(os.path.join(out, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    total_rows = sum(manifest["rows"].values())
    size_gb = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out) if f.endswith(".csv")) / 1e9
    log(f"done: {total_rows:,} rows, {size_gb:.2f} GB of CSV, {time.time() - t_start:.0f}s. Manifest: {out}/MANIFEST.json")


if __name__ == "__main__":
    main()
