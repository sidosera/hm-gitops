#!/usr/bin/env bash
# Create a dedicated SSH key pair for GitOps CI (never use your personal ~/.ssh/id_*).
#
#   ./scripts/new-gitops-deploy-key.sh
#   ./scripts/new-gitops-deploy-key.sh ~/.ssh/hm-gitops-deploy
#
# Then append the .pub line to the VPS user's ~/.ssh/authorized_keys and run:
#   gh secret set GITOPS_DEPLOY_KEY < path/to/private-key

set -euo pipefail
KEY="${1:-$HOME/.ssh/hm-gitops-deploy}"
if [[ -f "$KEY" ]]; then
  echo "Refusing to overwrite existing key: $KEY" >&2
  exit 1
fi
mkdir -p "$(dirname "$KEY")"
ssh-keygen -t ed25519 -f "$KEY" -C "hm-gitops-deploy" -N ""
chmod 600 "$KEY"
echo "Private: $KEY"
echo "Public:  $KEY.pub"
echo ""
echo "1) On the VPS (as secrets.deploy.ssh_user), append this line to ~/.ssh/authorized_keys:"
echo "----"
cat "$KEY.pub"
echo "----"
echo ""
echo "2) Upload only the deploy private key to GitHub (not your personal key):"
echo "   gh secret set GITOPS_DEPLOY_KEY < $KEY"
echo ""
echo "3) Remove legacy secret GITOPS_SSH_KEY from the repo if you set it earlier:"
echo "   gh secret delete GITOPS_SSH_KEY"
