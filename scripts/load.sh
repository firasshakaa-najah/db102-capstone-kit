#!/usr/bin/env bash
# Create the schema and bulk-load the generated CSVs.
#
#   scripts/load.sh                     # Docker: uses `docker compose exec mysql`
#   MYSQL="mysql -uroot -p" scripts/load.sh   # a local server; DATA_DIR must be readable by mysqld
#                                        # and inside its secure_file_priv folder
set -euo pipefail
cd "$(dirname "$0")/.."

DATA_DIR="${DATA_DIR:-/data}"                   # path AS SEEN BY THE SERVER
MYSQL="${MYSQL:-docker compose exec -T mysql mysql -uroot -proot}"

if [ ! -f data/orders.csv ] && [ "$DATA_DIR" = "/data" ]; then
  echo "No data/orders.csv found. Run the generator first:  python generator/generate.py --scale 0.05" >&2
  exit 1
fi

echo "== schema"
$MYSQL < sql/schema.sql

echo "== load from $DATA_DIR (this is the slow part; scale 1 takes a while)"
start=$(date +%s)
sed "s#'/data/#'${DATA_DIR}/#g" sql/load.sql | $MYSQL
echo "== loaded in $(( $(date +%s) - start ))s"

echo "== verify"
$MYSQL < sql/verify.sql
