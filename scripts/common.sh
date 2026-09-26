# shellcheck shell=bash
# Shared by every script in this folder: which database to use, and how to connect.
#
# Target (first argument of every script, or DB102_TARGET):
#   lab   (default)  database shopdb        CSVs in ./data       scale 0.2 - labs and live demos
#   full             database shopdb_full   CSVs in ./data-full  scale 1   - the course dataset
#
# Connection (environment variables, all optional):
#   DB102_USER (root)   DB102_HOST (127.0.0.1)   DB102_PORT (3306)
#   DB102_PASSWORD      asked once if not set; export DB102_PASSWORD='' for "no password"

DB102_TARGET="${DB102_TARGET:-lab}"
if [ "${1:-}" = "lab" ] || [ "${1:-}" = "full" ]; then
  DB102_TARGET="$1"
  DB102_ARG_USED=1
elif [ -n "${1:-}" ] && [ "${1#-}" = "${1}" ]; then
  echo "Unknown target '$1': use 'lab' (scale 0.2) or 'full' (scale 1)." >&2
  exit 2
else
  DB102_ARG_USED=0
fi
case "$DB102_TARGET" in
  lab)  DB102_DB=shopdb;      DB102_DATA=data ;;
  full) DB102_DB=shopdb_full; DB102_DATA=data-full ;;
  *)    echo "The target must be 'lab' or 'full' (got '$DB102_TARGET')." >&2; exit 2 ;;
esac

DB102_USER="${DB102_USER:-root}"
DB102_HOST="${DB102_HOST:-127.0.0.1}"
DB102_PORT="${DB102_PORT:-3306}"

if ! command -v mysql >/dev/null 2>&1; then
  echo "The 'mysql' command-line client was not found. Install MySQL 8.4 (see README, step 1) and try again." >&2
  exit 1
fi

if [ -z "${DB102_PASSWORD+x}" ]; then
  if { : > /dev/tty; } 2>/dev/null; then
    printf 'MySQL password for %s@%s (press Enter if none): ' "$DB102_USER" "$DB102_HOST" > /dev/tty
    IFS= read -r -s DB102_PASSWORD < /dev/tty
    echo > /dev/tty
  else
    DB102_PASSWORD=""
  fi
fi

# Credentials go into a private temporary option file (never on the command line), removed on exit.
DB102_CNF="$(mktemp)"
chmod 600 "$DB102_CNF"
trap 'rm -f "$DB102_CNF"' EXIT
{
  echo "[client]"
  echo "user=$DB102_USER"
  echo "password=\"${DB102_PASSWORD//\"/\\\"}\""
  echo "host=$DB102_HOST"
  echo "port=$DB102_PORT"
  echo "[mysql]"
  echo "local-infile=1"
} > "$DB102_CNF"

MYSQL="mysql --defaults-extra-file=$DB102_CNF"

# Run a SQL file (or stdin) against the target database: the name shopdb in it becomes $DB102_DB.
#   db102_sql FILE [mysql args...]
db102_sql() {
  local file="${1:-/dev/stdin}"
  [ $# -gt 0 ] && shift
  sed -E "s/shopdb([^_[:alnum:]]|\$)/${DB102_DB}\\1/g" "$file" | $MYSQL ${1+"$@"}
}
