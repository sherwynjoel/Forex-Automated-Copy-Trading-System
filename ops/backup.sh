#!/usr/bin/env bash
# Nightly Postgres backup for MirrorFleet.
#
# The database is the only irreplaceable state on the host: broker OAuth
# grants (encrypted), account roles and multipliers, position mappings, and
# the audit trail. Container images rebuild from git; this does not.
#
# Install on the host (as the deploy user):
#   crontab -e
#   15 2 * * *  bash /home/ubuntu/mirrorfleet/ops/backup.sh >> /home/ubuntu/backup.log 2>&1
#
# Invoked through `bash` on purpose: git does not preserve the executable
# bit on a Windows checkout, so the file may arrive non-executable.
# RUN IT ONCE BY HAND as the cron user before trusting it -- all three
# classic failures here (no docker permission, missing compose plugin,
# unwritable BACKUP_DIR) are silent inside cron.
#
# Restore:
#   gunzip -c mirrorfleet-YYYY-MM-DD.sql.gz \
#     | sudo docker compose exec -T postgres psql -U copytrader -d copytrader
#
# Note the dump contains encrypted broker tokens; it is only as safe as the
# FERNET_KEY, which lives in .env and is NOT in this dump. Keep a copy of
# .env somewhere safe and separate -- without it a restored dump cannot
# decrypt a single token.

set -euo pipefail

# Cron has no TTY, so a password prompt would hang forever. Use sudo only
# when this user actually needs it; add yourself to the `docker` group and
# this resolves to plain `docker`.
if docker info >/dev/null 2>&1; then
    DOCKER="docker"
else
    DOCKER="sudo -n docker"
fi

COMPOSE_DIR="${COMPOSE_DIR:-/home/ubuntu/mirrorfleet}"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

# Dumps contain password hashes and encrypted broker grants: never let one
# exist world-readable, not even for the seconds it is being written.
umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y-%m-%d_%H%M)"
out="$BACKUP_DIR/mirrorfleet-$stamp.sql.gz"
partial="$out.part"
# A failed dump must never be able to pose as the newest backup, so it is
# written under .part and only renamed once it has been checked.
trap 'rm -f "$partial"' EXIT

cd "$COMPOSE_DIR"
# pg_dump inside the container; compressed on the way out. --clean so the
# dump can be replayed over an existing database. pipefail (set above)
# makes a pg_dump failure fail the whole pipeline rather than yielding a
# valid gzip of nothing.
$DOCKER compose exec -T postgres \
    pg_dump -U copytrader --clean --if-exists copytrader | gzip -9 > "$partial"

# Belt to pipefail's braces: a dump that died mid-stream can still exit 0
# in odd cases, and a few hundred bytes is not a database.
size=$(stat -c%s "$partial")
if [ "$size" -lt 10000 ]; then
    echo "FAILED: dump is only ${size} bytes - not keeping it"
    exit 1
fi

mv "$partial" "$out"
trap - EXIT

# Keep the newest N by COUNT, never by age: pruning on age alone would
# delete every backup after KEEP_DAYS of silent failures.
ls -1t "$BACKUP_DIR"/mirrorfleet-*.sql.gz 2>/dev/null \
    | tail -n "+$((KEEP_DAYS + 1))" | xargs -r rm -f
echo "ok: $out ($size bytes), keeping the newest $KEEP_DAYS"
