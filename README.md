# DB-102 capstone kit — the multi-tenant e-commerce platform

Everything week 2 promises the rest of the semester: a MySQL 8.4 schema for a
Shopify-like platform, a deterministic data generator (~50M orders and events
across ~200 stores of very different sizes), a bulk loader, and the "application"
workload that week 3 diagnoses and week 4 tunes.

```
db102-capstone-kit/
├── docker-compose.yml          two MySQL 8.4 servers (lab :3306, full :3307), performance_schema + slow log on
├── mysql/conf.d/db102.cnf      the server settings (deliberately modest; week 4 tunes them)
├── sql/schema.sql              the schema: tenants, stores, products, customers, orders, order_items, payments, events
├── sql/load.sql                LOAD DATA INFILE for every table, then ANALYZE TABLE
├── sql/verify.sql              row counts, store skew, indexes, sizes
├── sql/analysis.sql            week 3: rank the workload (sys.statement_analysis, p95, full scans, slow log)
├── generator/generate.py       python generate.py --scale 0.2  (./data)  |  --scale 1 --target full  (./data-full)
├── generator/requirements.txt  numpy, pandas, faker, pymysql
├── workload/run_workload.py    the app: 21 query shapes, N threads, weighted mix, some deliberately bad
├── workload/queries.sql        the same shapes with literal parameters, for EXPLAIN by hand
├── scripts/load.sh, dump.sh    load = schema + data + verify;  dump = mysqldump snapshot  (add "full" for the scale-1 server)
├── scripts/db.sh               a mysql client on either server:  scripts/db.sh [lab|full] [mysql args]
└── scripts/target.sh           the lab/full switch the other scripts share
```

## Two servers: lab (scale 0.2) and full (scale 1)

The kit runs two independent MySQL servers side by side. Same image, same settings, same
database name (`shopdb`), same schema and queries; only the amount of data differs.

| target | Docker service | port | CSV folder | dataset | used for |
|--------|----------------|-----:|------------|---------|----------|
| `lab`  | `mysql`        | 3306 | `./data`      | `--scale 0.2`: 1.2M orders, 8.8M events | labs and live demos: every "before" query finishes in 0.3–10 s |
| `full` | `mysql-full`   | 3307 | `./data-full` | `--scale 1`: 6M orders, 44M events | the course dataset: the Milestone 1 report and the lecture numbers |

Every script takes the target as its first argument (or `DB102_TARGET=lab|full`; `lab` is the
default), and the Python tools take `--target lab|full`. Each server has its own data volume and
buffer pool, so one never pollutes the other's timings; stop the one you are not measuring
(`docker compose stop mysql-full`) if memory is tight. Proportions (store 1 = 12 % of orders,
refunds ≈ 3 %, …) are identical at both scales because the generator uses the same seed.

## Quick start (Docker)

```bash
# 0. requirements: Docker Desktop with >= 4 GB for containers (6 GB to run both servers at once),
#    Python 3.10+, ~3 GB free disk for the lab server, ~20 GB more for the full one
pip install -r generator/requirements.txt

# 1. the lab server (scale 0.2)
docker compose up -d                                   # wait until `docker compose ps` says healthy
python generator/generate.py --scale 0.2               # ~14M rows, ~1 min, ~1.3 GB of CSV in ./data
scripts/load.sh                                        # schema, LOAD DATA INFILE, ANALYZE TABLE, verify.sql

# 2. the full server (scale 1) - same steps with the "full" target
docker compose up -d mysql-full
python generator/generate.py --scale 1 --target full   # ~78M rows, ~8 min, ~6.5 GB of CSV in ./data-full
scripts/load.sh full                                   # 15-40 min on a laptop

# 3. the workload (the thing you diagnose in week 3)
python workload/run_workload.py --threads 8 --duration 300                  # lab
python workload/run_workload.py --threads 8 --duration 300 --target full    # full

# 4. rank it, from the database's own evidence
scripts/db.sh lab  -t < sql/analysis.sql
scripts/db.sh full -t < sql/analysis.sql
```

Connect with `scripts/db.sh` (lab) or `scripts/db.sh full`, or from any client on
`127.0.0.1:3306` (lab) / `127.0.0.1:3307` (full) as `app` / `app` (root / root for
performance_schema and sys). To run a SQL file or the week-3 examples against one server:
`MYSQL="docker compose exec -T mysql-full mysql -uroot -proot shopdb" ...`.

Only need one size? Everything still works with the lab server alone: `docker compose up -d`
starts only `mysql`, and `mysql-full` starts only when you name it (or pass `--profile full`).
The full server's buffer pool can be raised on its own, without touching `db102.cnf`:
`DB102_FULL_BUFFER_POOL=4G docker compose up -d mysql-full`.

## Sizes and timings

| scale | orders | events | all rows | CSV | InnoDB on disk | generate | load |
|------:|-------:|-------:|---------:|----:|---------------:|---------:|-----:|
| 0.02  | 120k   | 880k   | 1.4M     | 0.13 GB | 0.17 GB | 9 s (measured) | 9 s (measured) |
| 0.05  | 300k   | 2.2M   | 3.6M     | 0.32 GB | 0.41 GB | 20 s (measured) | 23 s (measured) |
| 0.2 (lab)  | 1.2M | 8.8M | ~14M | ~1.3 GB | — | ~1 min | ~2 min |
| 1 (full)   | 6M     | 44M    | ~78M     | ~6.5 GB | ~9 GB   | ~8 min | 15–40 min on a laptop |

Measured on a 2-core sandbox; scale 1 numbers are extrapolated. The load is
single-threaded `LOAD DATA INFILE`; the events table is the slow part. Keep the
CSVs after loading only if you plan to reload (week 7 will give you a better way).

The data is the same for every team: seed 42, same skew, same "worst queries".
`data/MANIFEST.json` records the row counts and the five biggest stores.

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

## Week 3 lab flow (the diagnosis loop)

1. **Capture** — the slow log (`long_query_time = 0.5`, `log_slow_extra = ON`) and
   `performance_schema` are already on. Reset the window before a measured run:
   `TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;`
2. **Load** — `python workload/run_workload.py --threads 8 --duration 300`. The runner
   prints its own client-side ranking at the end; compare it with the server's view.
3. **Rank** — `sql/analysis.sql`: total latency, rows examined per row sent, p95/p99,
   full scans, temp tables, sorts, and what is running *right now* (`sys.session`,
   `EXPLAIN FOR CONNECTION`, `KILL QUERY`).
4. **Explain** — `workload/queries.sql` has every shape with literal parameters:
   `EXPLAIN FORMAT=TREE ...`, then `EXPLAIN ANALYZE ...`. Read bottom-up.
5. **Hypothesize** — which access path or join side is wrong, and why. Write it down.
6. **Fix and measure** (week 4) — one index at a time, `TRUNCATE` the digest table,
   run the workload again, keep the before/after plans for the M1 report.

Every SELECT in the runner carries `MAX_EXECUTION_TIME(20000)`, so at scale 1 the
clickstream queries time out at 20 s instead of hanging a thread. Timeouts are counted
(`t/o` column) — a real application would do the same, and the slow log still records them.

`run_workload.py --list` prints the mix. `--heavy 0` turns the report/analytics queries
off (a smoke test), `--only order_by_number,recent_orders_store` isolates shapes.

## Without Docker

Any MySQL 8.0.18+ works (8.4 preferred). Put the settings from `mysql/conf.d/db102.cnf`
in your server config, make sure `secure_file_priv` points at (or contains) the CSV
folder and that the server process can read it, then:

```bash
DATA_DIR=/absolute/path/to/data MYSQL="mysql -uroot -p" scripts/load.sh
python workload/run_workload.py --user root --password ...
```

## Troubleshooting

* **`ERROR 1290 ... --secure-file-priv`** — the server may only read CSVs from its
  `secure_file_priv` folder. In Docker that is `/data` (the `./data` bind mount);
  outside Docker set `DATA_DIR` to a folder inside it.
* **`ERROR 13 ... Can't get stat of '/data/orders.csv'`** — the files are not readable by
  the `mysql` user inside the container; `chmod o+r data/*.csv`.
* **The container dies with "Cannot allocate memory"** — give Docker more memory or lower
  `innodb_buffer_pool_size` in `db102.cnf` (512M works; the lab will feel it).
* **Timeouts everywhere at scale 1** — expected for the four clickstream queries; that is
  the lab. If *point lookups* time out too, the buffer pool is starving: week 4.
* **Reloading** — `scripts/load.sh [lab|full]` drops and recreates every table; `scripts/dump.sh [lab|full]`
  snapshots the loaded database as `dumps/shopdb-<target>-<date>.sql.gz` (restore command printed).
* **Port 3307 already in use** — change the left side of `"3307:3306"` in `docker-compose.yml`
  and pass the same port to the workload runner with `--port`.
