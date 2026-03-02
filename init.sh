#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:?Usage: $0 <domain> <repo-url> [branch] [github-user]}"
REPO_URL="${2:?Usage: $0 <domain> <repo-url> [branch] [github-user]}"
BRANCH="${3:-main}"
GITHUB_USER="${4:-}"
APP_NAME="${DOMAIN%%.*}"

log() { printf '[%s-init] %s\n' "$APP_NAME" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# --------------------------------------------------------------------
# OS compatibility check
# --------------------------------------------------------------------
[[ "$(uname -s)" == "Linux" ]] || die "Unsupported OS: $(uname -s) (Linux required)"
[[ "$(uname -m)" == "x86_64" ]] || die "Unsupported architecture: $(uname -m) (x86_64 required)"
[[ -f /etc/os-release ]] || die "/etc/os-release not found"
. /etc/os-release
[[ "$ID" == "ubuntu" || "$ID_LIKE" == *"ubuntu"* ]] || die "Unsupported distro: $ID (Ubuntu required)"
if [[ "${VERSION_ID%%.*}" -lt 22 ]]; then
  die "Unsupported Ubuntu version: $VERSION_ID (22.04+ required)"
fi

# --------------------------------------------------------------------
# Packages
# --------------------------------------------------------------------
sudo apt-get update -y
sudo apt-get install -y git curl ufw fail2ban unattended-upgrades

# --------------------------------------------------------------------
# K3s
# --------------------------------------------------------------------
if ! command -v k3s >/dev/null 2>&1; then
  log "Installing k3s"
  curl -sfL https://get.k3s.io | sh -s - \
    --write-kubeconfig-mode=644
fi

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

log "Waiting for node"
for _ in $(seq 1 30); do
  k3s kubectl get nodes 2>/dev/null | grep -q ' Ready' && break
  sleep 2
done
k3s kubectl get nodes | grep -q ' Ready' || die "k3s not ready"

# -------------------------------------------------------------------
# TLS
# -------------------------------------------------------------------

if ! k3s kubectl get ns cert-manager >/dev/null 2>&1; then
  log "Installing cert-manager"
  k3s kubectl apply --server-side \
    -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
  k3s kubectl wait --for=condition=Available deployment --all \
    -n cert-manager --timeout=120s
fi

# --------------------------------------------------------------------
# ArgoCD
# --------------------------------------------------------------------
if ! k3s kubectl get ns argocd >/dev/null 2>&1; then
  log "Installing ArgoCD"
  k3s kubectl create namespace argocd
  k3s kubectl apply -n argocd --server-side \
    -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  k3s kubectl wait --for=condition=Available deployment --all \
    -n argocd --timeout=180s
fi

if ! k3s kubectl get deployment argocd-image-updater-controller -n argocd >/dev/null 2>&1; then
  log "Installing ArgoCD Image Updater"
  k3s kubectl apply -n argocd --server-side \
    -f https://raw.githubusercontent.com/argoproj-labs/argocd-image-updater/stable/config/install.yaml
  k3s kubectl wait --for=condition=Available deployment/argocd-image-updater-controller \
    -n argocd --timeout=120s
fi

k3s kubectl apply --server-side -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ${APP_NAME}
  namespace: argocd
  annotations:
    argocd-image-updater.argoproj.io/image-list: site=hub.${DOMAIN}/getrafty-org/site
    argocd-image-updater.argoproj.io/site.update-strategy: latest
    argocd-image-updater.argoproj.io/write-back-method: git
spec:
  project: default
  source:
    repoURL: ${REPO_URL}
    targetRevision: ${BRANCH}
    path: k8s
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
EOF

# --------------------------------------------------------------------
# Access & Security
# --------------------------------------------------------------------

REAL_USER="${SUDO_USER:-$(whoami)}"
REAL_HOME=$(eval echo "~${REAL_USER}")

if [[ -n "$GITHUB_USER" ]]; then
  log "Importing SSH keys for github.com/${GITHUB_USER} → ${REAL_USER}"
  mkdir -p "${REAL_HOME}/.ssh" && chmod 700 "${REAL_HOME}/.ssh"
  curl -fsSL "https://github.com/${GITHUB_USER}.keys" >> "${REAL_HOME}/.ssh/authorized_keys"
  chmod 600 "${REAL_HOME}/.ssh/authorized_keys"
  chown -R "${REAL_USER}:${REAL_USER}" "${REAL_HOME}/.ssh"
fi

sudo ufw allow OpenSSH >/dev/null
sudo ufw allow 80/tcp  >/dev/null
sudo ufw allow 443/tcp >/dev/null
sudo ufw allow 6443/tcp >/dev/null
sudo ufw --force enable >/dev/null
sudo systemctl enable --now fail2ban
sudo dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null || true

sudo tee "/etc/ssh/sshd_config.d/99-${APP_NAME}.conf" >/dev/null <<'EOF'
PasswordAuthentication no
X11Forwarding no
MaxAuthTries 3
EOF
sudo systemctl reload ssh

log "Ok"
