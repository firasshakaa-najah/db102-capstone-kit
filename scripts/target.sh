# shellcheck shell=bash
# Sourced by the other scripts: which of the two servers to talk to.
#
#   DB102_TARGET=lab   (default) service "mysql",      port 3306, CSVs in ./data       (scale 0.2)
#   DB102_TARGET=full            service "mysql-full", port 3307, CSVs in ./data-full  (scale 1)
#
# Every script also accepts the target as its first argument:  scripts/load.sh full
if [ "${1:-}" = "lab" ] || [ "${1:-}" = "full" ]; then
  DB102_TARGET="$1"
  shift
fi
DB102_TARGET="${DB102_TARGET:-lab}"
case "$DB102_TARGET" in
  lab)  DB102_SERVICE=mysql;      DB102_PORT=3306; DB102_LOCAL_DATA=data ;;
  full) DB102_SERVICE=mysql-full; DB102_PORT=3307; DB102_LOCAL_DATA=data-full ;;
  *)    echo "DB102_TARGET must be 'lab' or 'full' (got '$DB102_TARGET')" >&2; exit 2 ;;
esac
export DB102_TARGET DB102_SERVICE DB102_PORT DB102_LOCAL_DATA
