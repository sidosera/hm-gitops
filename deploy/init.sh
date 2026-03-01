#!/usr/bin/env bash
set -euo pipefail

log() { printf '[hackamonth-init] %s\n' "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

REPO_URL="${REPO_URL:-https://github.com/sidosera/machine-gitops.git}"
BRANCH="${BRANCH:-main}"
DOMAIN="${DOMAIN:-hackamonth.io}"
INSTALL_DIR="${INSTALL_DIR:-/srv/hackamonth}"
STATE_DIR="${STATE_DIR:-/var/lib/hackamonth}"
CFG_DIR="${CFG_DIR:-/etc/hackamonth}"
BIN_PATH="${BIN_PATH:-/usr/local/sbin/hackamonth-deploy}"
PURGE="${PURGE:-0}"

# --- packages ---

sudo apt-get update -y
sudo apt-get install -y git curl ufw fail2ban unattended-upgrades

# --- k3s ---

if ! command -v k3s >/dev/null 2>&1; then
  log "Installing k3s"
  curl -sfL https://get.k3s.io | sh -s - --disable=servicelb --write-kubeconfig-mode=644
fi

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

log "Waiting for node"
for _ in $(seq 1 30); do
  k3s kubectl get nodes 2>/dev/null | grep -q ' Ready' && break
  sleep 2
done
k3s kubectl get nodes | grep -q ' Ready' || die "k3s not ready"

# --- cert-manager ---

if ! k3s kubectl get ns cert-manager >/dev/null 2>&1; then
  log "Installing cert-manager"
  k3s kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
  k3s kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=120s
fi

# --- harden ---

sudo ufw allow OpenSSH >/dev/null
sudo ufw allow 80/tcp  >/dev/null
sudo ufw allow 443/tcp >/dev/null
sudo ufw allow 6443/tcp >/dev/null
sudo ufw --force enable >/dev/null
sudo systemctl enable --now fail2ban
sudo dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null || true

sudo tee /etc/ssh/sshd_config.d/99-hackamonth.conf >/dev/null <<'EOF'
PasswordAuthentication no
X11Forwarding no
MaxAuthTries 3
EOF
sudo systemctl reload ssh

# --- repo ---

sudo mkdir -p "$INSTALL_DIR" "$CFG_DIR" "$STATE_DIR"

if [ "$PURGE" = "1" ]; then
  log "Purging"
  k3s kubectl delete namespace hackamonth --ignore-not-found || true
  sudo rm -rf "${INSTALL_DIR:?}/"*
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  sudo git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  sudo git -C "$INSTALL_DIR" fetch --prune origin
else
  sudo git clone "$REPO_URL" "$INSTALL_DIR"
fi
sudo git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"

# --- wiring ---

sudo chmod 0755 "$INSTALL_DIR/deploy/deploy.sh"
sudo ln -sf "$INSTALL_DIR/deploy/deploy.sh" "$BIN_PATH"

sudo tee "$CFG_DIR/deploy.env" >/dev/null <<EOF
INSTALL_DIR=$INSTALL_DIR
STATE_DIR=$STATE_DIR
BRANCH=$BRANCH
DOMAIN=$DOMAIN
KUBECONFIG=/etc/rancher/k3s/k3s.yaml
EOF
sudo ln -sf "$CFG_DIR/deploy.env" /etc/default/hackamonth-deploy

sudo systemctl stop hackamonth-deploy.timer 2>/dev/null || true
sudo rm -f /etc/systemd/system/hackamonth-deploy.{service,timer}
sudo ln -s "$INSTALL_DIR/deploy/systemd/hackamonth-deploy.service" /etc/systemd/system/
sudo ln -s "$INSTALL_DIR/deploy/systemd/hackamonth-deploy.timer"   /etc/systemd/system/
sudo systemctl daemon-reload

# --- go ---

sudo systemctl start hackamonth-deploy.service
sudo systemctl enable --now hackamonth-deploy.timer

log "Done. k3s kubectl get all -n hackamonth"
