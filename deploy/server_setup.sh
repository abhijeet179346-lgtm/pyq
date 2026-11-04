#!/usr/bin/env bash
# One-time setup for Amazon Linux 2023 (run as ec2-user, needs sudo). Idempotent.
# Prerequisite: ~/.ssh/pyq_deploy.pub is added to the GitHub repo as a read-only deploy key.
#   PUBLIC_HOST=65.1.100.164 bash server_setup.sh
set -euo pipefail

PUBLIC_HOST=${PUBLIC_HOST:?Set PUBLIC_HOST to the server IP or domain}
REPO=${REPO:-git@github.com:abhijeet179346-lgtm/pyq.git}
APP_ROOT=/opt/pyq
APP_DIR=$APP_ROOT/app
PYTHON=python3.12
export DJANGO_SETTINGS_MODULE=config.settings.base

log() { printf '\n==> %s\n' "$*"; }

log "Packages"
sudo dnf install -y -q $PYTHON $PYTHON-pip git nginx sqlite >/dev/null

if ! swapon --show | grep -q .; then
  log "Adding 1 GB swap (server has under 1 GB RAM)"
  sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap defaults 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

log "GitHub access"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[ -f ~/.ssh/pyq_deploy ] || ssh-keygen -q -t ed25519 -N '' -C "pyq-deploy@$PUBLIC_HOST" -f ~/.ssh/pyq_deploy
grep -q 'IdentityFile ~/.ssh/pyq_deploy' ~/.ssh/config 2>/dev/null || cat >> ~/.ssh/config <<'EOF'
Host github.com
    User git
    IdentityFile ~/.ssh/pyq_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh-keygen -F github.com >/dev/null || ssh-keyscan -t ed25519 github.com 2>/dev/null >> ~/.ssh/known_hosts
if ! ssh -o BatchMode=yes -T git@github.com 2>&1 | grep -q 'successfully authenticated'; then
  echo "GitHub rejected the deploy key. Add this as a read-only deploy key, then re-run:" >&2
  cat ~/.ssh/pyq_deploy.pub >&2
  exit 1
fi

log "Code"
sudo mkdir -p $APP_ROOT && sudo chown "$USER": $APP_ROOT
mkdir -p $APP_ROOT/media $APP_ROOT/backups
[ -d $APP_DIR/.git ] || git clone -q --branch main "$REPO" $APP_DIR

log "Environment file"
if [ ! -f $APP_DIR/.env ]; then
  umask 077
  cat > $APP_DIR/.env <<EOF
DJANGO_SECRET_KEY=$($PYTHON -c 'import secrets;print(secrets.token_urlsafe(50))')
DJANGO_ALLOWED_HOSTS=$PUBLIC_HOST
PYQ_CORE_ORIGIN=http://$PUBLIC_HOST
PYQ_TENANT_BASE_DOMAIN=$PUBLIC_HOST
PYQ_MEDIA_ROOT=$APP_ROOT/media
# No SMTP configured yet: verification emails are written to the service log.
DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EOF
  umask 022
fi

log "Services"
sudo cp $APP_DIR/deploy/pyq.service /etc/systemd/system/pyq.service
sed "s/__PUBLIC_HOST__/$PUBLIC_HOST/" $APP_DIR/deploy/nginx-pyq.conf | sudo tee /etc/nginx/conf.d/pyq.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable -q pyq nginx
sudo nginx -t -q
sudo systemctl start nginx
if [ "$(getenforce 2>/dev/null || echo Disabled)" = Enforcing ]; then
  sudo setsebool -P httpd_can_network_connect 1
fi
chmod 755 $APP_ROOT $APP_DIR   # nginx reads staticfiles

log "First deploy"
bash $APP_DIR/deploy/deploy.sh

cd $APP_DIR
if [ "$($APP_ROOT/venv/bin/python manage.py shell -c 'from apps.content.models import Question;print(Question.objects.count())' 2>/dev/null | tail -1)" = 0 ]; then
  log "Seeding question banks, notes and timed tests"
  $APP_ROOT/venv/bin/python manage.py import_pyq_sample --tenant master
  $APP_ROOT/venv/bin/python manage.py import_exam_banks --tenant master
  $APP_ROOT/venv/bin/python manage.py enrich_question_metadata
  $APP_ROOT/venv/bin/python manage.py seed_revision_notes
  $APP_ROOT/venv/bin/python manage.py seed_timed_tests
fi
log "Done: http://$PUBLIC_HOST/"
