#!/usr/bin/env bash
# A mysql client on shopdb, on either server. Extra arguments go to mysql.
#
#   scripts/db.sh                         # interactive client on the lab server
#   scripts/db.sh full                    # interactive client on the full server
#   scripts/db.sh full -t < sql/analysis.sql
#   scripts/db.sh lab -e "SELECT COUNT(*) FROM orders"
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/target.sh
source scripts/target.sh "$@"
[ "${1:-}" = "$DB102_TARGET" ] && shift
tty_flag=""
[ -t 0 ] || tty_flag="-T"
exec docker compose exec $tty_flag "$DB102_SERVICE" mysql -uroot -proot shopdb "$@"
