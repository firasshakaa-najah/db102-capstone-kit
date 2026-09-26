#!/usr/bin/env bash
# A mysql client on the lab or full database. Extra arguments go to mysql.
#
#   scripts/db.sh                              # interactive, on shopdb (lab)
#   scripts/db.sh full                         # interactive, on shopdb_full
#   scripts/db.sh full --table < sql/analysis.sql
#   scripts/db.sh lab -e "SELECT COUNT(*) FROM orders"
#
# A file piped in is run against the chosen database: "shopdb" in it becomes shopdb_full for full.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/common.sh
source scripts/common.sh "$@"
[ "$DB102_ARG_USED" = 1 ] && shift
piped=0
if [ ! -t 0 ]; then
  piped=1
  for a in ${1+"$@"}; do case "$a" in -e|--execute|--execute=*|-e*) piped=0 ;; esac; done
fi
if [ "$piped" = 1 ]; then
  db102_sql /dev/stdin "$DB102_DB" ${1+"$@"}
else
  $MYSQL "$DB102_DB" ${1+"$@"}
fi
