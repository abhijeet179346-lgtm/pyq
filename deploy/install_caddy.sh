#!/usr/bin/env bash
# Install Caddy, serve the app over HTTPS, and retire nginx. Safe to re-run.
#   PUBLIC_HOST=65.1.100.164 bash /opt/pyq/app/deploy/install_caddy.sh
set -euo pipefail
APP_DIR=${APP_DIR:-/opt/pyq/app}
PUBLIC_HOST=${PUBLIC_HOST:?set PUBLIC_HOST to the server IP or domain}
CADDY_VERSION=${CADDY_VERSION:-2.11.9}
log() { printf '\n==> %s\n' "$*"; }

if ! /usr/local/bin/caddy version 2>/dev/null | grep -q "v$CADDY_VERSION"; then
  log "Installing Caddy $CADDY_VERSION"
  case $(uname -m) in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) echo "unsupported arch" >&2; exit 1 ;; esac
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  base=https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION
  tarball=caddy_${CADDY_VERSION}_linux_$arch.tar.gz
  curl -fsSL -o "$tmp/$tarball" "$base/$tarball"
  curl -fsSL -o "$tmp/checksums" "$base/caddy_${CADDY_VERSION}_checksums.txt"
  (cd "$tmp" && grep " $tarball\$" checksums | sha512sum -c --quiet)
  tar -xzf "$tmp/$tarball" -C "$tmp" caddy
  sudo install -m 755 "$tmp/caddy" /usr/local/bin/caddy
fi

log "Configuring Caddy for $PUBLIC_HOST"
id caddy >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/caddy --create-home --shell /sbin/nologin caddy
sudo mkdir -p /etc/caddy
sed "s/__PUBLIC_HOST__/$PUBLIC_HOST/" "$APP_DIR/deploy/Caddyfile" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo /usr/local/bin/caddy fmt --overwrite /etc/caddy/Caddyfile
sudo /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile >/dev/null
sudo cp "$APP_DIR/deploy/caddy.service" /etc/systemd/system/caddy.service

log "Switching the app to HTTPS"
env_set() { grep -q "^$1=" "$APP_DIR/.env" && sed -i "s|^$1=.*|$1=$2|" "$APP_DIR/.env" || echo "$1=$2" >> "$APP_DIR/.env"; }
env_set PYQ_CORE_ORIGIN "https://$PUBLIC_HOST"
env_set DJANGO_HTTPS true

log "Swapping nginx for Caddy"
if systemctl list-unit-files nginx.service >/dev/null 2>&1; then
  sudo systemctl disable --now nginx 2>/dev/null || true
fi
sudo systemctl daemon-reload
sudo systemctl enable -q caddy
sudo systemctl restart caddy pyq

log "Waiting for the certificate"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null --resolve "$PUBLIC_HOST:443:127.0.0.1" "https://$PUBLIC_HOST/login/" 2>/dev/null; then
    echo "OK: https://$PUBLIC_HOST/ is serving with a trusted certificate"; exit 0
  fi
  sleep 2
done
echo "HTTPS not ready yet. Caddy log:" >&2
sudo journalctl -u caddy -n 40 --no-pager >&2
exit 1
