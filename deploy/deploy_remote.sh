#!/usr/bin/env bash
# Deploy origin/main to the server from your machine:
#   git push origin main && deploy/deploy_remote.sh
set -euo pipefail
HOST=${HOST:-65.1.100.164}
KEY=${KEY:-$(cd "$(dirname "$0")/.." && pwd)/pyq.pem}
ssh -i "$KEY" -o IdentitiesOnly=yes ec2-user@"$HOST" 'bash /opt/pyq/app/deploy/deploy.sh'
