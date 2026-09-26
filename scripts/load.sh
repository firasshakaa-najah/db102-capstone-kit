#!/usr/bin/env bash
# Create the schema and bulk-load the generated CSVs.
#
#   scripts/load.sh                     # lab server  (Docker service "mysql",      CSVs from ./data)
#   scripts/load.sh full                # full server (Docker service "mysql-full", CSVs from ./data-full)
#   MYSQL="mysql -uroot -p" scripts/load.sh   # a local server; DATA_DIR must be readable by mysqld
#                                        # and inside its secure_file_priv folder
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/target.sh
source scripts/target.sh "$@"

DATA_DIR="${DATA_DIR:-/data}"                   # path AS SEEN BY THE SERVER
MYSQL="${MYSQL:-docker compose exec -T $DB102_SERVICE mysql -uroot -proot}"

if [ ! -f "$DB102_LOCAL_DATA/orders.csv" ] && [ "$DATA_DIR" = "/data" ]; then
  if [ "$DB102_TARGET" = "full" ]; then hint="--scale 1 --target full"; else hint="--scale 0.2"; fi
  echo "No $DB102_LOCAL_DATA/orders.csv found. Run the generator first:  python generator/generate.py $hint" >&2
  exit 1
fi

echo "== target: $DB102_TARGET ($DB102_SERVICE, port $DB102_PORT)"
echo "== schema"
$MYSQL < sql/schema.sql

echo "== load from $DATA_DIR (this is the slow part; scale 1 takes a while)"
start=$(date +%s)
sed "s#'/data/#'${DATA_DIR}/#g" sql/load.sql | $MYSQL
echo "== loaded in $(( $(date +%s) - start ))s"

echo "== verify"
$MYSQL < sql/verify.sql
