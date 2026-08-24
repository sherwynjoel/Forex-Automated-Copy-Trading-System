#!/usr/bin/env bash
# Provision a fresh MirrorFleet host (Ubuntu 24.04) in a new region.
#
# Run this ON THE NEW BOX, as the `ubuntu` user, BEFORE any data is moved.
# It installs Docker and Caddy, clones the repo, and writes the Caddy config
# -- everything that does not involve secrets or the database.
#
# It deliberately does NOT:
#   - copy .env               (secrets; you scp that yourself, see step 3)
#   - restore the database    (a separate, checkable step)
#   - start Caddy             (it cannot get a certificate until DNS moves)
#   - touch DNS               (the cutover is a decision, not a script)
#
# Usage:
#   bash provision-new-host.sh https://github.com/<you>/<repo>.git
#
set -euo pipefail

REPO_URL="${1:-}"
if [ -z "$REPO_URL" ]; then
    echo "usage: bash provision-new-host.sh <git-repo-url>" >&2
    exit 1
fi

DOMAIN="${DOMAIN:-mirrorfleet.com}"
APP_DIR="${APP_DIR:-$HOME/mirrorfleet}"

echo "==> 1/5 base packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    docker.io docker-compose-v2 git curl \
    debian-keyring debian-archive-keyring apt-transport-https

# Lets this user run docker without sudo (takes effect on next login).
sudo usermod -aG docker "$USER"

echo "==> 2/5 caddy (automatic TLS)"
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq caddy
fi

# Caddy must NOT run yet: with DNS still pointing at the old box it would
# fail the ACME challenge repeatedly and can get rate-limited by Let's
# Encrypt. It is enabled at cutover instead.
sudo systemctl disable --now caddy 2>/dev/null || true

echo "==> 3/5 caddy config for ${DOMAIN}"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
${DOMAIN} {
	reverse_proxy 127.0.0.1:8000
}
www.${DOMAIN} {
	redir https://${DOMAIN}{uri} permanent
}
EOF
sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null && echo "    Caddyfile valid"

echo "==> 4/5 clone repo"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --quiet origin && git -C "$APP_DIR" reset --quiet --hard origin/main
else
    git clone --quiet "$REPO_URL" "$APP_DIR"
fi

echo "==> 5/5 build images"
# `migrate` is built BY NAME on purpose: it bakes db/migrations into its
# image, so building only api+copier silently ships an out-of-date schema.
cd "$APP_DIR"
sudo docker compose build api copier migrate

cat <<'NEXT'

Provisioned. Nothing is running yet, and that is intentional.

NEXT STEPS (in order, from your own machine):

  1. Copy secrets across -- FERNET_KEY decrypts every broker token, so a
     restore without it means every account must reconnect from scratch:

       scp ubuntu@<OLD-IP>:~/mirrorfleet/.env ./mirrorfleet.env.bak
       scp ./mirrorfleet.env.bak ubuntu@<NEW-IP>:~/mirrorfleet/.env

     Then delete the BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD lines
     from the new .env -- they are only for a first-ever install.

     Verify all five critical keys landed (prints a count, not the values):

       grep -cE '^(FERNET_KEY|SESSION_SECRET|POSTGRES_PASSWORD|CTRADER_CLIENT_ID|CTRADER_CLIENT_SECRET)=' ~/mirrorfleet/.env
       # must print: 5

  2. Restore the database, then run restore-check.sh to confirm it matches.

  3. Test through a hosts-file entry BEFORE moving DNS.

  4. Cut over at weekend market close, with no open positions.

NEXT
