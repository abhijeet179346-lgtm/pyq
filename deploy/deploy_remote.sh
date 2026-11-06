#!/usr/bin/env bash
# Deploy origin/main to the server from your machine:
#   git push origin main && deploy/deploy_remote.sh
set -euo pipefail
HOST=${HOST:-35.77.200.154}   # Tokyo, same region as Supabase
KEY=${KEY:-$(cd "$(dirname "$0")/.." && pwd)/pyq-tokyo.pem}
ssh -i "$KEY" -o IdentitiesOnly=yes ec2-user@"$HOST" 'bash /opt/pyq/app/deploy/deploy.sh'
