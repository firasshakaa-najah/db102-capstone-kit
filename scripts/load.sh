#!/usr/bin/env bash
# Create the database and bulk-load the generated CSVs, then verify.
# Drops and recreates the tables, so it is also the way to start again from scratch.
#
#   scripts/load.sh          # lab:  ./data       -> database shopdb
#   scripts/load.sh full     # full: ./data-full  -> database shopdb_full
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/common.sh
source scripts/common.sh "$@"

if [ ! -f "$DB102_DATA/orders.csv" ]; then
  if [ "$DB102_TARGET" = "full" ]; then gen="--target full"; else gen=""; fi
  echo "No $DB102_DATA/orders.csv yet. Generate the data first:  python3 generator/generate.py $gen" >&2
  exit 1
fi
csv_dir="$(cd "$DB102_DATA" && pwd)"

echo "== $DB102_TARGET: loading $DB102_DATA/ into database $DB102_DB"
echo "== schema"
db102_sql sql/schema.sql

echo "== data (the slow part: ~2 min for lab, 15-40 min for full)"
start=$(date +%s)
sed "s#'/data/#'${csv_dir}/#g" sql/load.sql | db102_sql
echo "== loaded in $(( $(date +%s) - start ))s"

echo "== verify"
db102_sql sql/verify.sql --table
