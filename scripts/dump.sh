#!/usr/bin/env bash
# Snapshot a loaded database as a compressed SQL dump, e.g. the "before any tuning" baseline.
#
#   scripts/dump.sh          # -> dumps/shopdb-<date>.sql.gz
#   scripts/dump.sh full     # -> dumps/shopdb_full-<date>.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/common.sh
source scripts/common.sh "$@"
mkdir -p dumps
out="dumps/$DB102_DB-$(date +%Y%m%d-%H%M).sql.gz"
mysqldump --defaults-extra-file="$DB102_CNF" --single-transaction --quick --routines --set-gtid-purged=OFF "$DB102_DB" | gzip > "$out"
ls -lh "$out"
echo "restore with:  gunzip -c $out | scripts/db.sh $DB102_TARGET"
