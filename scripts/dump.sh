#!/usr/bin/env bash
# Snapshot the loaded database as a compressed SQL dump (share it between teams,
# or keep it as the "before any tuning" baseline for week 4 and the week 7 drills).
#
#   scripts/dump.sh                 # -> dumps/shopdb-<date>.sql.gz  (Docker)
#   MYSQLDUMP="mysqldump -uroot -p" scripts/dump.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dumps
MYSQLDUMP="${MYSQLDUMP:-docker compose exec -T mysql mysqldump -uroot -proot}"
out="dumps/shopdb-$(date +%Y%m%d-%H%M).sql.gz"
$MYSQLDUMP --single-transaction --quick --routines --set-gtid-purged=OFF shopdb | gzip > "$out"
ls -lh "$out"
echo "restore with:  gunzip -c $out | docker compose exec -T mysql mysql -uroot -proot shopdb"
