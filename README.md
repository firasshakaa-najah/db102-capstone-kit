# DB-102 Capstone Kit

The database you will work on all semester: a multi-tenant e-commerce platform (like Shopify)
with about 200 stores, their products, customers, orders, payments and clickstream events.

The kit gives you:

- **the schema**: 8 tables in MySQL 8.4
- **a data generator**: the same data for every team (seed 42)
- **a loader**: loads the generated data and checks it
- **a workload**: a small "application" that runs the platform's queries, some of them slow on purpose. Week 3 diagnoses them; week 4 fixes them.

## Two datasets

You get the same data at two sizes, loaded as two databases on your MySQL server:

| Name   | Database      | Size                                     | Use it for |
|--------|---------------|------------------------------------------|------------|
| `lab`  | `shopdb`      | scale 0.2: 1.2M orders, 8.8M events      | labs and trying things out: every query answers within seconds |
| `full` | `shopdb_full` | scale 1: 6M orders, 44M events           | the course dataset: the Milestone 1 report and the lecture numbers |

Start with `lab`. Load `full` when you need it. Every script uses `lab` unless you add `full`.

---

## Setup (once)

### 1. Install what you need

- **MySQL 8.4** (server and the `mysql` command-line client)
  - macOS: `brew install mysql@8.4 && brew link --force mysql@8.4 && brew services start mysql@8.4`
  - Ubuntu / Debian: `sudo apt install mysql-server` (8.0.30 or newer also works)
  - Windows: use **WSL** (Ubuntu) and follow the Ubuntu steps inside it
- **Python 3.10 or newer**
- **Disk space**: about 3 GB for `lab`, about 20 GB more for `full`

Check that MySQL is running:

```bash
mysql -uroot -p -e "SELECT VERSION();"
```

### 2. Get the kit and its Python packages

```bash
git clone https://github.com/firasshakaa-najah/db102-capstone-kit.git
cd db102-capstone-kit
python3 -m venv .venv                      # a private Python environment for the kit
source .venv/bin/activate                  # run this again in every new terminal
pip install -r generator/requirements.txt
```

On Ubuntu / Debian, if `python3 -m venv` fails, install it first: `sudo apt install python3-venv`.

### 3. Prepare the server

```bash
scripts/setup.sh
```

This applies the course settings (slow query log on, buffer pool size, …), creates the two
empty databases and the `app` user (password `app`) that the workload connects as.

The scripts ask for your MySQL root password once each time you run them. To skip the question,
set it for your terminal session: `export DB102_PASSWORD='your-password'` (or `''` if root has
no password).

### 4. Create and load the lab dataset

```bash
python3 generator/generate.py      # about 1 minute, writes CSV files into data/
scripts/load.sh                    # about 2 minutes, loads them into shopdb and checks them
```

You now have a working `shopdb`. That is all you need for the labs.

### 5. Create and load the full dataset (when you need it)

```bash
python3 generator/generate.py --target full    # about 8 minutes, writes CSV files into data-full/
scripts/load.sh full                           # 15–40 minutes, loads them into shopdb_full
```

After loading you can delete the CSV files to save space; generate them again if you ever need to reload.

---

## Everyday use

| To …                                    | lab                                    | full |
|-----------------------------------------|----------------------------------------|------|
| open a MySQL prompt on the database     | `scripts/db.sh`                        | `scripts/db.sh full` |
| run a SQL file                          | `scripts/db.sh --table < file.sql`     | `scripts/db.sh full --table < file.sql` |
| run the workload for 5 minutes          | `python3 workload/run_workload.py --duration 300` | `python3 workload/run_workload.py --duration 300 --target full` |
| rank the slow queries                   | `scripts/db.sh --table < sql/analysis.sql` | `scripts/db.sh full --table < sql/analysis.sql` |
| start again from a clean database       | `scripts/load.sh`                      | `scripts/load.sh full` |
| save a backup                           | `scripts/dump.sh`                      | `scripts/dump.sh full` |

SQL files in this kit say `USE shopdb`. When you run one with `scripts/db.sh full`, it runs on
`shopdb_full` automatically.

You can also connect with any tool (MySQL Workbench, DBeaver, DataGrip, VS Code) to
`127.0.0.1:3306` as root, or as `app` / `app`, and pick `shopdb` or `shopdb_full`.

Both databases live on the same server and share its memory. When you measure timings, run
one workload at a time.

---

## Week 3: finding the slow queries

1. **Reset the counters** before each measured run:
   `scripts/db.sh -e "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest"`
2. **Run the workload**: `python3 workload/run_workload.py --threads 8 --duration 300`.
   At the end it prints its own ranking; compare it with the server's.
3. **Rank**: `scripts/db.sh --table < sql/analysis.sql` shows total time, rows examined per
   row sent, p95/p99, full scans, temporary tables, sorts, and what is running right now.
4. **Explain**: `workload/queries.sql` has every query with real values. Run
   `EXPLAIN FORMAT=TREE …`, then `EXPLAIN ANALYZE …`, and read the plan bottom-up.
5. **Write down a hypothesis**: which access path or join is wrong, and why.
6. **Fix and measure** (week 4): one index at a time, reset the counters, run the workload
   again, and keep the before/after plans for the Milestone 1 report.

Useful workload options: `--list` prints the query mix, `--heavy 0` turns off the report
queries, `--only order_by_number,recent_orders_store` runs just those queries.

Every read query in the workload stops after 20 seconds. On `full`, the clickstream queries hit that
limit; they are counted as timeouts (`t/o`), just as a real application would count them.

---

## Sizes and timings

| scale        | orders | events | all rows | CSV     | generate | load |
|-------------:|-------:|-------:|---------:|--------:|---------:|-----:|
| 0.2 (`lab`)  | 1.2M   | 8.8M   | ~14M     | ~1.3 GB | ~1 min   | ~2 min |
| 1 (`full`)   | 6M     | 44M    | ~78M     | ~6.5 GB | ~8 min   | 15–40 min on a laptop |

Other sizes: `python3 generator/generate.py --scale 0.05 --out some/folder`. Proportions are the
same at every size (store 1 has 12% of all orders, refunds are about 3% of payments).

---

## What is in the kit

```
db102-capstone-kit/
├── generator/generate.py      creates the CSV files (lab → data/, full → data-full/)
├── scripts/setup.sh           once: server settings, databases, app user
├── scripts/load.sh            creates the tables, loads the CSVs, checks the result
├── scripts/db.sh              MySQL prompt, or runs a SQL file, on lab or full
├── scripts/dump.sh            backup of a database into dumps/
├── sql/schema.sql             the tables
├── sql/load.sql               the bulk load (LOAD DATA LOCAL INFILE)
├── sql/verify.sql             row counts, store sizes, indexes, table sizes
├── sql/analysis.sql           week 3: rank the workload's queries
├── sql/server_settings.sql    the course settings applied by setup.sh
├── workload/run_workload.py   the application: 21 kinds of queries on N threads
├── workload/queries.sql       the same queries with real values, for EXPLAIN
└── docs/instructor-notes.md   background for the instructor
```

---

## Troubleshooting

- **`error: externally-managed-environment`** when running `pip install`: you skipped the
  virtual environment. Run `python3 -m venv .venv && source .venv/bin/activate`, then `pip install` again.
- **`ModuleNotFoundError: No module named 'numpy'`** (or `pymysql`, `faker`): the virtual environment
  is not active in this terminal. Run `source .venv/bin/activate`.
- **`mysql: command not found`**: the client is not on your PATH. On macOS run
  `brew link --force mysql@8.4`, then open a new terminal.
- **`Access denied for user 'root'`** on Ubuntu: there root can only log in with `sudo mysql`.
  Create a user for the kit and use it:
  ```bash
  sudo mysql -e "CREATE USER 'db102'@'127.0.0.1' IDENTIFIED BY 'db102';
                 GRANT ALL ON *.* TO 'db102'@'127.0.0.1' WITH GRANT OPTION;"
  export DB102_USER=db102 DB102_PASSWORD=db102
  ```
- **`Loading local data is disabled`**: run `scripts/setup.sh` first; it turns on `local_infile`.
- **MySQL on another port or machine**: `export DB102_HOST=… DB102_PORT=…` for the scripts, and
  `--host … --port …` for the workload.
- **Point lookups time out on `full` too**: the buffer pool is too small for the data. That is
  week 4's topic.
- **Where is the slow query log?** `scripts/db.sh -e "SELECT @@slow_query_log_file"`. The file
  belongs to the MySQL server, so you may need `sudo` to read it.
