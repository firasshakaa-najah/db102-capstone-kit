#!/usr/bin/env python3
"""
DB-102 · Assignment 1 evidence collector.

Run it from the kit folder, with the kit's virtual environment active, AFTER you have run
scripts/setup.sh, generator/generate.py and scripts/load.sh:

    source .venv/bin/activate
    python3 scripts/evidence.py

It asks for your student ID and your name, connects to your MySQL server exactly like the
other scripts do (DB102_USER / DB102_PASSWORD / DB102_HOST / DB102_PORT, root by default),
reads the state of the server and of the shopdb database, and prints an evidence block.
The same output is also written to evidence-<student id>.txt in the kit folder.

Submit the block exactly as printed (everything between the BEGIN and END lines).
The block ends with a SHA-256 of its content, so it must not be edited by hand.

The script only READS: it changes nothing on your server and nothing in the kit.
Options:  --student-id ID  --name "Full Name"  --host  --port  --user  --kit PATH  --out FILE
"""
import argparse
import getpass
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

FORMAT = "db102-evidence/1"
ASSIGNMENT = "A1"
BEGIN = "-----BEGIN DB102 EVIDENCE-----"
END = "-----END DB102 EVIDENCE-----"

TABLES = ["tenants", "stores", "products", "customers", "orders", "order_items", "payments", "events"]

# The course settings that scripts/setup.sh applies (sql/server_settings.sql).
EXPECTED_SETTINGS = {
    "innodb_buffer_pool_size": "1073741824",
    "innodb_redo_log_capacity": "536870912",
    "innodb_flush_log_at_trx_commit": "2",
    "local_infile": "1",
    "slow_query_log": "1",
    "long_query_time": "0.500000",
    "log_slow_extra": "1",
    "log_queries_not_using_indexes": "0",
    "max_connections": "300",
}
PERSISTED_NAMES = sorted(EXPECTED_SETTINGS)

# The indexes sql/schema.sql creates, plus the ones InnoDB adds for the foreign keys.
EXPECTED_INDEXES = sorted([
    "customers.PRIMARY(id):unique", "customers.uq_customers_store_email(store_id,email):unique",
    "events.PRIMARY(id):unique",
    "order_items.PRIMARY(id):unique", "order_items.fk_items_order(order_id)", "order_items.fk_items_product(product_id)",
    "orders.PRIMARY(id):unique", "orders.fk_orders_customer(customer_id)", "orders.fk_orders_store(store_id)",
    "payments.PRIMARY(id):unique", "payments.fk_payments_order(order_id)",
    "products.PRIMARY(id):unique", "products.uq_products_store_sku(store_id,sku):unique",
    "stores.PRIMARY(id):unique", "stores.fk_stores_tenant(tenant_id)", "stores.uq_stores_slug(slug):unique",
    "tenants.PRIMARY(id):unique",
])

# Columns hashed for the row samples: only values that come from the generator's seeded
# numeric stream (no names, e-mails or cities, which depend on the Faker version).
SAMPLE_COLUMNS = {
    "tenants": "id, plan, country_code, created_at",
    "stores": "id, tenant_id, currency, created_at",
    "products": "id, tenant_id, store_id, sku, category, price, stock_qty, is_active, created_at",
    "customers": "id, tenant_id, store_id, country_code, created_at",
    "orders": "id, tenant_id, store_id, customer_id, order_number, status, channel, currency, subtotal, "
              "shipping_fee, discount, total, item_count, shipping_country, created_at, updated_at",
    "order_items": "id, tenant_id, order_id, product_id, quantity, unit_price, line_total",
    "payments": "id, tenant_id, order_id, provider, status, amount, currency, IFNULL(provider_ref,''), created_at",
    "events": "id, tenant_id, store_id, IFNULL(customer_id,''), session_id, event_type, IFNULL(product_id,''), "
              "IFNULL(order_id,''), device, country_code, occurred_at",
}
SAMPLE_SIZE = 40
KIT_FILES = ["sql/schema.sql", "sql/load.sql", "sql/server_settings.sql", "sql/verify.sql",
             "scripts/setup.sh", "scripts/load.sh", "scripts/common.sh", "generator/generate.py"]


# ----------------------------------------------------------------------------- helpers
def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def s(v):
    """Every value becomes a JSON-safe string or int, so the canonical JSON is stable everywhere."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v.microsecond == 0 else v.strftime("%Y-%m-%d %H:%M:%S.%f")
    return str(v)


def same(a, b):
    """Compare two setting values, numerically when both are numbers ('0.5' == '0.500000')."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def dump(obj, indent=0, width=92):
    """Readable JSON: one key per line, lists of scalars packed on wrapped lines (valid JSON)."""
    pad = " " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [pad + " " + json.dumps(k) + ": " + dump(obj[k], indent + 1, width) for k in sorted(obj)]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in obj):
            parts = [json.dumps(x, ensure_ascii=True) for x in obj]
            lines, cur = [], ""
            for p in parts:
                if cur and len(cur) + len(p) + 2 > width:
                    lines.append(cur)
                    cur = ""
                cur += (", " if cur else "") + p
            lines.append(cur)
            if len(lines) == 1:
                return "[" + lines[0] + "]"
            return "[\n" + ",\n".join(pad + " " + ln for ln in lines) + "\n" + pad + "]"
        return "[\n" + ",\n".join(pad + " " + dump(x, indent + 1, width) for x in obj) + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=True)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def run_cmd(args, cwd):
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


class Check:
    def __init__(self):
        self.items = []   # (status, text)

    def ok(self, text):
        self.items.append(("ok", text))

    def warn(self, text):
        self.items.append(("!!", text))

    def render(self):
        return "\n".join("  [%s] %s" % (st, tx) for st, tx in self.items)


# ----------------------------------------------------------------------------- client side
def client_info(kit):
    info = {
        "hostname": socket.gethostname(),
        "os_user": None,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "cwd": os.getcwd(),
        "kit_path": kit,
        "timezone": time.strftime("%Z"),
        "utc_offset_min": int((datetime.now().astimezone().utcoffset() or 0).total_seconds() // 60)
        if datetime.now().astimezone().utcoffset() is not None else None,
        "cpu_count": os.cpu_count(),
        "ram_mb": None,
        "is_wsl": 0,
        "distro": None,
        "mac_ver": None,
        "venv": os.environ.get("VIRTUAL_ENV"),
        "packages": {},
    }
    try:
        info["os_user"] = getpass.getuser()
    except Exception:
        pass
    try:
        info["ram_mb"] = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1048576)
    except Exception:
        pass
    try:
        if os.path.exists("/proc/version"):
            with open("/proc/version") as f:
                pv = f.read().lower()
            info["is_wsl"] = int("microsoft" in pv or "wsl" in pv)
        if os.environ.get("WSL_DISTRO_NAME"):
            info["is_wsl"] = 1
            info["distro"] = "WSL:" + os.environ["WSL_DISTRO_NAME"]
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        name = line.split("=", 1)[1].strip().strip('"')
                        info["distro"] = (info["distro"] + " / " + name) if info["distro"] else name
        if platform.system() == "Darwin":
            info["mac_ver"] = platform.mac_ver()[0]
    except Exception:
        pass
    for mod in ("numpy", "pandas", "faker", "pymysql"):
        try:
            m = __import__(mod)
            # PyMySQL's __version__ is its MySQLdb-compatibility number; VERSION_STRING is the real one
            v = getattr(m, "VERSION_STRING", None) or getattr(m, "__version__", None) or getattr(m, "VERSION", "?")
            info["packages"][mod] = str(v)
        except Exception:
            info["packages"][mod] = None
    return info


def kit_info(kit):
    info = {
        "git_head": run_cmd(["git", "rev-parse", "HEAD"], kit),
        "git_commit_date": run_cmd(["git", "log", "-1", "--format=%cI"], kit),
        "git_remote": run_cmd(["git", "config", "--get", "remote.origin.url"], kit),
        "git_modified": None,
        "file_sha256": {},
    }
    # core.fileMode=false: permission-only changes (common under /mnt/c on WSL) are not modifications
    st = run_cmd(["git", "-c", "core.fileMode=false", "status", "--porcelain", "--untracked-files=no"], kit)
    if st is not None:
        info["git_modified"] = sorted(line[3:].strip() for line in st.splitlines() if line.strip())[:30]
    for rel in KIT_FILES:
        p = os.path.join(kit, rel)
        info["file_sha256"][rel] = sha256_file(p) if os.path.isfile(p) else None
    return info


def data_info(kit, log):
    data_dir = os.path.join(kit, "data")
    info = {"dir": data_dir, "manifest": None, "csv": {}}
    mp = os.path.join(data_dir, "MANIFEST.json")
    if os.path.isfile(mp):
        try:
            with open(mp) as f:
                m = json.load(f)
            # floats are kept out of the evidence (stable canonical JSON): shares become strings
            m["biggest_stores"] = [[b.get("store_id"), str(b.get("order_share"))] for b in m.get("biggest_stores", [])]
            m["scale"] = str(m.get("scale"))
            info["manifest"] = m
            info["manifest_mtime_utc"] = datetime.fromtimestamp(os.path.getmtime(mp), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            info["manifest"] = {"error": str(e)}
    for t in TABLES:
        p = os.path.join(data_dir, t + ".csv")
        if os.path.isfile(p):
            size = os.path.getsize(p)
            log("  hashing data/%s.csv (%d MB) ..." % (t, size // 1048576))
            # [bytes, modification time (UTC), sha256]
            info["csv"][t] = [size, datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                              sha256_file(p)]
        else:
            info["csv"][t] = None
    return info


# ----------------------------------------------------------------------------- server side
class DB:
    def __init__(self, conn):
        self.conn = conn
        self.timings = {}
        self.errors = {}

    def rows(self, name, sql, args=None):
        t = time.time()
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, args)
                out = [tuple(s(v) for v in r) for r in cur.fetchall()]
        except Exception as e:
            self.errors[name] = "%s: %s" % (type(e).__name__, e)
            out = None
        self.timings[name] = int((time.time() - t) * 1000)
        return out

    def one(self, name, sql, args=None):
        r = self.rows(name, sql, args)
        return r[0] if r else None


def collect_server(db):
    out = {}
    # variables every MySQL-like server has (MariaDB too), so the version is always reported
    r = db.one("server", "SELECT VERSION(), @@version_comment, @@version_compile_os, @@version_compile_machine, "
                         "@@hostname, @@port, @@datadir, @@socket, @@basedir, @@system_time_zone, "
                         "@@innodb_page_size, @@character_set_server, @@collation_server, CURRENT_USER(), "
                         "UTC_TIMESTAMP(), @@server_id, @@lower_case_table_names")
    if r:
        keys = ["version", "version_comment", "compile_os", "compile_machine", "hostname", "port",
                "datadir", "socket", "basedir", "system_time_zone", "innodb_page_size", "character_set_server",
                "collation_server", "current_user", "now_utc", "server_id", "lower_case_table_names"]
        out.update(dict(zip(keys, r)))
    r = db.one("server_uuid", "SELECT @@server_uuid")          # MySQL only: unique per data directory
    out["server_uuid"] = r[0] if r else None
    r = db.one("uptime", "SELECT variable_value FROM performance_schema.global_status WHERE variable_name='Uptime'")
    out["uptime_s"] = int(r[0]) if r and r[0] and str(r[0]).isdigit() else None
    return out


def is_mariadb(srv):
    return "mariadb" in ("%s %s" % (srv.get("version"), srv.get("version_comment"))).lower()


def collect_settings(db):
    names = PERSISTED_NAMES + ["slow_query_log_file", "performance_schema", "information_schema_stats_expiry",
                               "innodb_stats_persistent", "version_compile_os", "sql_mode", "time_zone"]
    out = {}
    for n in names:
        r = db.one("var_" + n, "SELECT @@GLOBAL.%s" % n)
        out[n] = r[0] if r else None
    persisted = {}
    r = db.rows("persisted", "SELECT variable_name, variable_value FROM performance_schema.persisted_variables ORDER BY 1")
    if r is not None:
        persisted = {k: v for k, v in r}
    vinfo = {}
    r = db.rows("variables_info", "SELECT variable_name, variable_source, set_time, set_user, set_host "
                                  "FROM performance_schema.variables_info WHERE variable_name IN (%s) ORDER BY 1"
                % ",".join("'%s'" % n for n in PERSISTED_NAMES + ["sql_log_bin", "foreign_key_checks", "unique_checks"]))
    if r is not None:
        # "source|set_time|set_user|set_host" per variable
        vinfo = {n: "|".join("" if v is None else str(v) for v in (src, st, u, h)) for n, src, st, u, h in r}
    return out, persisted, vinfo


def collect_databases(db):
    out = {}
    r = db.rows("schemata", "SELECT schema_name, default_character_set_name, default_collation_name "
                            "FROM information_schema.schemata WHERE schema_name IN ('shopdb','shopdb_full') ORDER BY 1")
    for name, cs, col in (r or []):
        out[name] = {"charset": cs, "collation": col}
    return out


def collect_app_user(db):
    out = {"accounts": [], "grants": {}}
    r = db.rows("app_accounts", "SELECT user, host FROM mysql.user WHERE user='app' ORDER BY host")
    if r is not None:
        out["accounts"] = ["%s@%s" % (u, h) for u, h in r]
    for acct in ("app@localhost", "app@127.0.0.1"):
        if acct not in out["accounts"]:
            continue
        u, h = acct.split("@")
        g = db.rows("grants_" + h, "SHOW GRANTS FOR %s@%s", (u, h))
        if g is not None:
            out["grants"][acct] = sorted(row[0] for row in g)
    return out


TABLE_FIELDS = ["engine", "collation", "row_format", "table_rows", "data_length", "index_length",
                "auto_increment", "create_time", "update_time", "stats_last_update", "stats_n_rows"]


def collect_tables(db):
    """{table: [values in TABLE_FIELDS order]} - times are UTC (session time_zone is +00:00)."""
    out = {}
    r = db.rows("tables", "SELECT table_name, engine, table_collation, row_format, table_rows, data_length, "
                          "index_length, auto_increment, create_time, update_time FROM information_schema.tables "
                          "WHERE table_schema='shopdb' ORDER BY table_name")
    for row in (r or []):
        out[row[0]] = list(row[1:]) + [None, None]
    st = db.rows("innodb_stats", "SELECT table_name, last_update, n_rows FROM mysql.innodb_table_stats "
                                 "WHERE database_name='shopdb' ORDER BY 1")
    for name, lu, n in (st or []):
        if name in out:
            out[name][9], out[name][10] = lu, n
    return out


def table_field(ev, table, field):
    row = ev["tables"].get(table)
    return row[TABLE_FIELDS.index(field)] if row else None


def collect_columns(db):
    r = db.rows("columns", "SELECT CONCAT_WS(':', table_name, column_name, column_type, is_nullable) "
                           "FROM information_schema.columns WHERE table_schema='shopdb' ORDER BY table_name, ordinal_position")
    cols = [x[0] for x in (r or [])]
    return cols, hashlib.sha256("|".join(cols).encode()).hexdigest()


def collect_indexes(db):
    r = db.rows("indexes", "SELECT table_name, index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index), MIN(non_unique) "
                           "FROM information_schema.statistics WHERE table_schema='shopdb' "
                           "GROUP BY table_name, index_name ORDER BY 1, 2")
    out = []
    for t, i, cols, nu in (r or []):
        out.append("%s.%s(%s)%s" % (t, i, cols, "" if str(nu) == "1" else ":unique"))
    return sorted(out)


def collect_counts_and_aggregates(db, log):
    counts, agg = {}, {}
    for t in TABLES:
        log("  counting shopdb.%s ..." % t)
        r = db.one("count_" + t, "SELECT COUNT(*), MAX(id) FROM shopdb.%s" % t)
        counts[t] = r[0] if r else None
        agg.setdefault(t, {})["max_id"] = r[1] if r else None

    log("  aggregating orders ...")
    r = db.one("agg_orders", "SELECT SUM(total), SUM(item_count), MIN(created_at), MAX(created_at), "
                             "COUNT(DISTINCT customer_id), COUNT(DISTINCT store_id) FROM shopdb.orders")
    if r:
        agg["orders"].update(dict(zip(["sum_total", "sum_item_count", "min_created_at", "max_created_at",
                                       "distinct_customers", "distinct_stores"], r)))
    r = db.rows("orders_by_status", "SELECT status, COUNT(*), SUM(total) FROM shopdb.orders GROUP BY status ORDER BY status")
    agg["orders"]["by_status"] = {st: [n, tot] for st, n, tot in (r or [])}
    r = db.rows("orders_top_stores", "SELECT store_id, COUNT(*) FROM shopdb.orders GROUP BY store_id "
                                     "ORDER BY 2 DESC, 1 LIMIT 5")
    agg["orders"]["top_stores"] = [[sid, n] for sid, n in (r or [])]

    log("  aggregating order_items, payments, products, customers, tenants, stores ...")
    r = db.one("agg_items", "SELECT SUM(quantity), SUM(line_total) FROM shopdb.order_items")
    if r:
        agg["order_items"].update({"sum_quantity": r[0], "sum_line_total": r[1]})
    r = db.rows("payments_by_status", "SELECT status, COUNT(*), SUM(amount) FROM shopdb.payments GROUP BY status ORDER BY status")
    agg["payments"]["by_status"] = {st: [n, tot] for st, n, tot in (r or [])}
    r = db.one("agg_products", "SELECT SUM(price), SUM(stock_qty), SUM(is_active) FROM shopdb.products")
    if r:
        agg["products"].update({"sum_price": r[0], "sum_stock_qty": r[1], "active": r[2]})
    r = db.one("agg_customers", "SELECT COUNT(DISTINCT store_id) FROM shopdb.customers")
    if r:
        agg["customers"]["distinct_stores"] = r[0]
    r = db.rows("tenants_by_plan", "SELECT plan, COUNT(*) FROM shopdb.tenants GROUP BY plan ORDER BY plan")
    agg["tenants"]["by_plan"] = {p: n for p, n in (r or [])}
    r = db.one("agg_stores", "SELECT COUNT(DISTINCT tenant_id) FROM shopdb.stores")
    if r:
        agg["stores"]["distinct_tenants"] = r[0]

    log("  aggregating events (the big table, this takes a little while) ...")
    r = db.one("agg_events", "SELECT MIN(occurred_at), MAX(occurred_at) FROM shopdb.events")
    if r:
        agg["events"].update({"min_occurred_at": r[0], "max_occurred_at": r[1]})
    r = db.rows("events_by_type", "SELECT event_type, COUNT(*) FROM shopdb.events GROUP BY event_type ORDER BY event_type")
    agg["events"]["by_type"] = {et: n for et, n in (r or [])}
    return counts, agg


def sample_ids(max_id, n=SAMPLE_SIZE):
    """A fixed pseudo-random sample of ids: the same for everybody with the same max_id.
    (xorshift32 in plain integer arithmetic, so it does not depend on the Python version.)"""
    if not max_id:
        return []
    max_id = int(max_id)
    x = ((max_id * 2654435761 + 20261) & 0xFFFFFFFF) | 1
    ids = {1, max_id}
    while len(ids) < min(n, max_id):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        ids.add(1 + x % max_id)
    return sorted(ids)


def collect_samples(db, agg, log):
    log("  hashing row samples ...")
    out = {}
    for t in TABLES:
        mid = agg.get(t, {}).get("max_id")
        ids = sample_ids(mid)
        if not ids:
            out[t] = None
            continue
        r = db.one("sample_" + t, "SELECT COUNT(*), SHA2(GROUP_CONCAT(CONCAT_WS(',', %s) ORDER BY id SEPARATOR '|'), 256) "
                                  "FROM shopdb.%s WHERE id IN (%s)" % (SAMPLE_COLUMNS[t], t, ",".join(map(str, ids))))
        # [rows found, sha256]; the ids are not stored, they follow from max_id (see sample_ids)
        out[t] = [r[0], r[1]] if r else None
    return out


# ----------------------------------------------------------------------------- self-checks
def self_check(ev, chk):
    srv, st, db_ = ev["server"], ev["settings"], ev["databases"]
    ver = srv.get("version") or ""
    try:
        parts = [int(x) for x in ver.split("-")[0].split(".")[:3]]
        ok_ver = parts >= [8, 0, 30]
    except Exception:
        parts, ok_ver = [], False
    if is_mariadb(srv):
        chk.warn("the server is MariaDB %s, not MySQL - install MySQL 8.4 (see the handout, step 1)" % ver)
    else:
        (chk.ok if ok_ver else chk.warn)("MySQL server %s on %s (%s)%s" % (
            ver or "?", srv.get("hostname"), srv.get("compile_os"),
            "" if ok_ver else " - the course needs 8.4 (8.0.30 or newer also works)"))

    bad = [k for k, v in EXPECTED_SETTINGS.items() if not same(st.get(k), v)]
    if bad:
        chk.warn("course settings differ from sql/server_settings.sql: %s - run scripts/setup.sh again" % ", ".join(bad))
    else:
        chk.ok("course settings applied (buffer pool 1G, slow log on, local_infile on, ...)")
    missing = [n for n in PERSISTED_NAMES if n not in ev["persisted"]]
    (chk.warn if missing else chk.ok)(
        "settings persisted with SET PERSIST (scripts/setup.sh)" if not missing else
        "settings not persisted: %s - run scripts/setup.sh (it uses SET PERSIST)" % ", ".join(missing))

    for name in ("shopdb", "shopdb_full"):
        d = db_.get(name)
        if d and d.get("collation") == "utf8mb4_0900_ai_ci":
            chk.ok("database %s exists (%s)" % (name, d["collation"]))
        else:
            chk.warn("database %s %s - run scripts/setup.sh" % (name, "missing" if not d else "has collation " + str(d.get("collation"))))
    accts = ev["app_user"]["accounts"]
    if "app@localhost" in accts and "app@127.0.0.1" in accts:
        chk.ok("user 'app' exists (app@localhost, app@127.0.0.1)")
    else:
        chk.warn("user 'app' accounts found: %s - run scripts/setup.sh" % (accts or "none"))

    man = ev["data"]["manifest"]
    if not man or "rows" not in man:
        chk.warn("data/MANIFEST.json not found - run python3 generator/generate.py from the kit folder")
    else:
        (chk.ok if str(man.get("scale")) == "0.2" and str(man.get("seed")) == "42" else chk.warn)(
            "data/MANIFEST.json: scale %s, seed %s, generated %s" % (man.get("scale"), man.get("seed"), man.get("generated_at")))
    csv_missing = [t for t in TABLES if not ev["data"]["csv"].get(t)]
    (chk.warn if csv_missing else chk.ok)(
        "CSV files present in data/" if not csv_missing else "CSV files missing in data/: %s (keep them until the assignment is graded)" % ", ".join(csv_missing))

    tables = ev["tables"]
    missing_t = [t for t in TABLES if t not in tables]
    if missing_t:
        chk.warn("tables missing in shopdb: %s - run scripts/load.sh" % ", ".join(missing_t))
    else:
        chk.ok("all 8 tables exist in shopdb")
    if man and "rows" in man and not missing_t:
        diff = [t for t in TABLES if ev["counts"].get(t) != man["rows"].get(t)]
        if diff == ["events"] and (ev["counts"].get("events") or 0) > (man["rows"].get("events") or 0):
            chk.warn("shopdb.events has %s more rows than were loaded: the workload (workload/run_workload.py) "
                     "inserts events. Run scripts/load.sh again before submitting" % format(
                         ev["counts"]["events"] - man["rows"]["events"], ","))
        elif diff:
            chk.warn("row counts differ from data/MANIFEST.json for: %s - run scripts/load.sh again" % ", ".join(diff))
        else:
            chk.ok("row counts match data/MANIFEST.json (%s orders, %s events)" % (
                format(ev["counts"]["orders"], ","), format(ev["counts"]["events"], ",")))
    extra = sorted(set(ev["indexes"]) - set(EXPECTED_INDEXES))
    lost = sorted(set(EXPECTED_INDEXES) - set(ev["indexes"]))
    if extra or lost:
        chk.warn("indexes differ from sql/schema.sql%s%s" % (
            " - extra: " + ", ".join(extra) if extra else "", " - missing: " + ", ".join(lost) if lost else ""))
    elif not missing_t:
        chk.ok("indexes are exactly the ones in sql/schema.sql (%d)" % len(EXPECTED_INDEXES))
    stale = []
    for t in TABLES:
        n = ev["counts"].get(t)
        est = table_field(ev, t, "stats_n_rows")
        if est is None:
            est = table_field(ev, t, "table_rows")
        if n and est is not None and abs(int(est) - n) > 0.10 * n:
            stale.append(t)
    if stale:
        chk.warn("optimizer statistics look stale for: %s (scripts/load.sh ends with ANALYZE TABLE)" % ", ".join(stale))
    elif not missing_t:
        chk.ok("optimizer statistics are fresh (ANALYZE TABLE ran)")

    mod = ev["kit"].get("git_modified")
    core = [f for f in (mod or []) if f.startswith(("sql/", "generator/", "scripts/"))]
    if ev["kit"].get("git_head") is None:
        chk.warn("kit folder is not a git clone - clone it with git so the version can be checked")
    elif core:
        chk.warn("modified kit files: %s (the assignment expects the kit unchanged)" % ", ".join(core))
    else:
        chk.ok("kit unchanged, commit %s" % (ev["kit"]["git_head"] or "?")[:12])
    denied = sorted(k for k, v in ev["errors"].items() if "denied" in v.lower())
    other = sorted(k for k in ev["errors"] if k not in denied)
    if denied:
        chk.warn("%d queries were denied (%s) - run the script as root (macOS) or db102 (Ubuntu / WSL), "
                 "like scripts/setup.sh" % (len(denied), ", ".join(denied)[:120]))
    if other:
        chk.warn("%d queries failed: %s" % (len(other), "; ".join("%s: %s" % (k, ev["errors"][k][:80]) for k in other[:3])))


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--student-id")
    ap.add_argument("--name")
    ap.add_argument("--host", default=os.environ.get("DB102_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DB102_PORT", "3306")))
    ap.add_argument("--user", default=os.environ.get("DB102_USER", "root"))
    ap.add_argument("--kit", default=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
    ap.add_argument("--out", help="where to write the evidence file (default: evidence-<student id>.txt in the kit folder)")
    args = ap.parse_args()

    def log(msg):
        print(msg, file=sys.stderr, flush=True)

    try:
        import pymysql
    except ImportError:
        log("pymysql is not installed in this Python. Activate the kit's virtual environment first:\n"
            "    source .venv/bin/activate\nthen run this script again.")
        sys.exit(2)

    try:
        student_id = (args.student_id or "").strip()
        while len(student_id) < 3:
            student_id = input("Your student ID: ").strip()
        name = (args.name or "").strip()
        while len(name) < 3:
            name = input("Your full name (as registered): ").strip()
        password = os.environ.get("DB102_PASSWORD")
        if password is None:
            password = getpass.getpass("MySQL password for %s@%s (press Enter if none): " % (args.user, args.host))
    except (EOFError, KeyboardInterrupt):
        log("\nCancelled. You can also pass --student-id, --name and export DB102_PASSWORD.")
        sys.exit(2)

    started = time.time()
    log("== connecting to MySQL on %s:%s as %s" % (args.host, args.port, args.user))
    kw = dict(host=args.host, port=args.port, user=args.user, password=password,
              charset="utf8mb4", connect_timeout=10, read_timeout=900)
    try:
        try:
            conn = pymysql.connect(**kw)
        except Exception as e:
            if "cryptography" not in str(e):
                raise
            # caching_sha2_password after a server restart, with a PyMySQL that does not use TLS by
            # default: retry over TLS (a local server, so the certificate is not checked)
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = pymysql.connect(ssl=ctx, **kw)
    except Exception as e:
        hint = ""
        if "cryptography" in str(e):
            hint = "\nInstall the missing package in the kit's environment:  pip install cryptography"
        log("Cannot connect: %s\nIs the server running? Same credentials as scripts/db.sh "
            "(DB102_USER / DB102_PASSWORD / DB102_HOST / DB102_PORT).%s" % (e, hint))
        sys.exit(1)
    db = DB(conn)
    db.rows("session_tz", "SET SESSION time_zone = '+00:00'")
    db.rows("session_gc", "SET SESSION group_concat_max_len = 1048576")

    log("== reading the kit folder (%s)" % args.kit)
    ev = {"format": FORMAT, "assignment": ASSIGNMENT,
          "student": {"id": student_id, "name": name},
          "collected_at_utc": utc_now(),
          "collected_at_local": datetime.now().replace(microsecond=0).isoformat(),
          "script_sha256": sha256_file(os.path.abspath(__file__)),
          "client": client_info(args.kit), "kit": kit_info(args.kit), "data": data_info(args.kit, log)}
    ev["client"].update({"db_host": args.host, "db_port": args.port, "db_user": args.user})
    log("== reading the server")
    ev["server"] = collect_server(db)
    ev["settings"], ev["persisted"], ev["variables_info"] = collect_settings(db)
    ev["databases"] = collect_databases(db)
    ev["app_user"] = collect_app_user(db)
    ev["tables_fields"] = TABLE_FIELDS
    ev["tables"] = collect_tables(db)
    ev["columns"], ev["schema_sha256"] = collect_columns(db)
    ev["indexes"] = collect_indexes(db)
    log("== reading shopdb")
    ev["counts"], ev["aggregates"] = collect_counts_and_aggregates(db, log)
    ev["samples"] = collect_samples(db, ev["aggregates"], log)
    conn.close()
    ev["timings_ms"] = {k: v for k, v in db.timings.items()
                        if k.startswith(("count_", "agg_", "sample_", "events_", "orders_", "payments_", "tenants_"))}
    ev["errors"] = db.errors
    ev["elapsed_s"] = int(time.time() - started)

    chk = Check()
    self_check(ev, chk)
    ev["self_check"] = [st + " " + tx for st, tx in chk.items]

    canonical = json.dumps(ev, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    pretty = dump(ev)
    assert json.loads(pretty) == ev

    n_warn = sum(1 for st, _ in chk.items if st == "!!")
    lines = [
        "",
        "DB-102 Assignment %s evidence  |  %s  |  %s (%s)" % (ASSIGNMENT, ev["collected_at_utc"], name, student_id),
        "MySQL %s on %s  |  client %s %s  |  %d s" % (ev["server"].get("version"), ev["server"].get("hostname"),
                                                        ev["client"]["system"], ev["client"]["machine"], ev["elapsed_s"]),
        "",
        chk.render(),
        "",
        "  %d checks passed, %d warnings.%s" % (len(chk.items) - n_warn, n_warn,
                                                "" if n_warn == 0 else " Fix the warnings and run the script again before submitting."),
        "",
        "Submit everything between the two lines below (or the file this was saved to):",
        "",
        BEGIN,
        pretty,
        "sha256:" + digest,
        END,
        "",
    ]
    text = "\n".join(lines)
    print(text)

    out = args.out or os.path.join(args.kit, "evidence-%s.txt" % "".join(c for c in student_id if c.isalnum() or c in "-_"))
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        log("== saved to %s" % out)
    except Exception as e:
        log("== could not save the evidence file (%s); copy it from the terminal instead" % e)


if __name__ == "__main__":
    main()
