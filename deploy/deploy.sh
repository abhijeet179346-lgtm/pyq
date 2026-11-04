#!/usr/bin/env bash
# Pull origin/main and deploy it on this server. Safe to re-run.
#   bash /opt/pyq/app/deploy/deploy.sh
set -euo pipefail

APP_ROOT=${APP_ROOT:-/opt/pyq}
APP_DIR=$APP_ROOT/app
VENV=$APP_ROOT/venv
BRANCH=${BRANCH:-main}
PYTHON=${PYTHON:-python3.12}
export DJANGO_SETTINGS_MODULE=config.settings.base

log() { printf '\n==> %s\n' "$*"; }
cd "$APP_DIR"

log "Backing up database"
mkdir -p "$APP_ROOT/backups"
if [ -f db.sqlite3 ]; then
  "$PYTHON" - <<EOF
import sqlite3, time
src = sqlite3.connect("db.sqlite3")
dst = sqlite3.connect("$APP_ROOT/backups/db-" + time.strftime("%Y%m%d-%H%M%S") + ".sqlite3")
src.backup(dst); dst.close(); src.close()
EOF
  ls -1t "$APP_ROOT"/backups/db-*.sqlite3 | tail -n +11 | xargs -r rm -f   # keep the 10 newest
fi

log "Pulling origin/$BRANCH"
git fetch --prune origin "$BRANCH"
git reset --hard "origin/$BRANCH"     # untracked .env, db.sqlite3 and staticfiles are kept
git log -1 --format='Deploying %h %s'

log "Installing dependencies"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt -r deploy/requirements-server.txt

log "Migrating and collecting static files"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput --verbosity 0
"$VENV/bin/python" manage.py check

log "Restarting service"
sudo systemctl restart pyq
sudo systemctl reload nginx

log "Health check"
for _ in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $(grep -E '^DJANGO_ALLOWED_HOSTS=' .env | cut -d= -f2 | cut -d, -f1)" http://127.0.0.1/login/ || true)
  [ "$code" = 200 ] && { echo "OK: /login/ returned 200"; exit 0; }
  sleep 1
done
echo "Health check failed (last status: $code). Recent logs:" >&2
sudo journalctl -u pyq -n 40 --no-pager >&2
exit 1
