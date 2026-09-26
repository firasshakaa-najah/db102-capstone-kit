#!/usr/bin/env bash
# One-time server setup: the course settings (slow log, buffer pool, ...) and the "app" user
# that the workload runner connects as. Safe to run again.
#
#   scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/common.sh
source scripts/common.sh "$@"

echo "== MySQL server $($MYSQL -N -e 'SELECT VERSION()') on $DB102_HOST:$DB102_PORT"
$MYSQL < sql/server_settings.sql
echo "== done: course settings applied, user 'app' (password 'app') can use shopdb and shopdb_full"
