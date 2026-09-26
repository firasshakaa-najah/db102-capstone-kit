#!/usr/bin/env bash
# Snapshot the loaded database as a compressed SQL dump (share it between teams,
# or keep it as the "before any tuning" baseline for week 4 and the week 7 drills).
#
#   scripts/dump.sh                 # lab server  -> dumps/shopdb-lab-<date>.sql.gz  (Docker)
#   scripts/dump.sh full            # full server -> dumps/shopdb-full-<date>.sql.gz
#   MYSQLDUMP="mysqldump -uroot -p" scripts/dump.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/target.sh
source scripts/target.sh "$@"
mkdir -p dumps
MYSQLDUMP="${MYSQLDUMP:-docker compose exec -T $DB102_SERVICE mysqldump -uroot -proot}"
out="dumps/shopdb-$DB102_TARGET-$(date +%Y%m%d-%H%M).sql.gz"
$MYSQLDUMP --single-transaction --quick --routines --set-gtid-purged=OFF shopdb | gzip > "$out"
ls -lh "$out"
echo "restore with:  gunzip -c $out | docker compose exec -T $DB102_SERVICE mysql -uroot -proot shopdb"
