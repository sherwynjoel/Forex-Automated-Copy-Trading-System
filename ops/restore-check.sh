#!/usr/bin/env bash
# Restore a MirrorFleet dump onto a new host and PROVE it landed intact.
#
# The proof matters more than the restore: a dump that replays without
# error can still be the wrong dump, or be missing the encryption key that
# makes its broker tokens usable. This compares the new database against
# the old one row-for-row on the tables that decide whether trading works,
# and refuses to declare success on a mismatch.
#
# Run ON THE NEW BOX:
#   bash restore-check.sh ~/restore.sql.gz ubuntu@<OLD-IP>
#
# The second argument is optional; without it the script restores and
# reports counts, but cannot compare them against the source.
#
set -euo pipefail

DUMP="${1:-}"
OLD_HOST="${2:-}"
APP_DIR="${APP_DIR:-$HOME/mirrorfleet}"

if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "usage: bash restore-check.sh <dump.sql.gz> [ubuntu@OLD-IP]" >&2
    exit 1
fi

cd "$APP_DIR"

# The counts that actually matter: users can log in, orgs exist, accounts
# are present, and -- the one people forget -- ctid_connections carry the
# encrypted broker grants. A restore that loses those looks fine until
# every account shows as disconnected.
QUERY="SELECT
  (SELECT count(*) FROM users) || '/' ||
  (SELECT count(*) FROM orgs) || '/' ||
  (SELECT count(*) FROM accounts) || '/' ||
  (SELECT count(*) FROM ctid_connections) || '/' ||
  (SELECT count(*) FROM events)"

echo "==> starting postgres"
sudo docker compose up -d postgres
# Wait for readiness rather than sleeping a guessed number of seconds.
for _ in $(seq 1 60); do
    if sudo docker compose exec -T postgres pg_isready -U copytrader -q 2>/dev/null; then break; fi
    sleep 1
done
sudo docker compose exec -T postgres pg_isready -U copytrader -q \
    || { echo "FAILED: postgres never became ready"; exit 1; }

echo "==> restoring $(basename "$DUMP")"
# The dump is taken with --clean --if-exists, so it drops and recreates
# each object; replaying it over an empty or populated database both work.
gunzip -c "$DUMP" | sudo docker compose exec -T postgres \
    psql -U copytrader -d copytrader -q -v ON_ERROR_STOP=1 \
    || { echo "FAILED: restore reported an error"; exit 1; }

echo "==> applying any migrations newer than the dump"
sudo docker compose run --rm migrate

NEW_COUNTS=$(sudo docker compose exec -T postgres psql -U copytrader -d copytrader -tAc "$QUERY" | tr -d ' ')
echo "    new host  users/orgs/accounts/connections/events = $NEW_COUNTS"

if [ -n "$OLD_HOST" ]; then
    OLD_COUNTS=$(ssh -o BatchMode=yes "$OLD_HOST" \
        "cd ~/mirrorfleet && sudo docker compose exec -T postgres psql -U copytrader -d copytrader -tAc \"$QUERY\"" \
        2>/dev/null | tr -d ' ')
    echo "    old host  users/orgs/accounts/connections/events = $OLD_COUNTS"

    if [ "$NEW_COUNTS" != "$OLD_COUNTS" ]; then
        echo
        echo "MISMATCH -- do not cut over."
        echo "The event count may legitimately differ (the old box keeps logging"
        echo "after the dump was taken). Any difference in users, orgs, accounts"
        echo "or connections is a real problem: re-take the dump and restore again."
        exit 1
    fi
    echo "    counts match"
fi

# FERNET_KEY is not in the dump -- it lives in .env. Without it the
# encrypted broker grants above are unreadable, and every account would
# have to reconnect. Check it is present before calling this a success.
if ! grep -q '^FERNET_KEY=.\+' "$APP_DIR/.env" 2>/dev/null; then
    echo
    echo "FAILED: FERNET_KEY is missing or empty in .env."
    echo "The broker tokens just restored cannot be decrypted without it."
    exit 1
fi
echo "    FERNET_KEY present"

cat <<'NEXT'

Restore verified.

Still to do before cutover -- do NOT skip the browser test:

  1. Start the stack:   sudo docker compose up -d
  2. Point ONLY your laptop at this box via the hosts file:
        <NEW-IP>  mirrorfleet.com
  3. Open https://mirrorfleet.com (accept the certificate warning -- DNS
     has not moved yet, so Caddy cannot have a valid one).
  4. Log in, open Accounts, and confirm every account reads CONNECTED.
     That is the real FERNET_KEY proof; the grep above only checks the key
     exists, not that it is the right one.
  5. Remove the hosts entry afterwards.

NEXT
