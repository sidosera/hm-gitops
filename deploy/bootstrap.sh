#!/usr/bin/env bash
set -euo pipefail

log() { printf '%s %s\n' "[hackamonth-bootstrap]" "$*"; }
die() { printf '%s %s\n' "[hackamonth-bootstrap][ERROR]" "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

need bash
need sudo
need curl


# Configuration

GH_USER="${GH_USER:-sidosera}"
REPO="${REPO:-machine-gitops}"
REPO_URL="${REPO_URL:-https://github.com/${GH_USER}/${REPO}.git}"

REF="${REF:-main}"

INSTALL_DIR="${INSTALL_DIR:-/srv/hackamonth}"
CFG_DIR="${CFG_DIR:-/etc/hackamonth}"
STATE_DIR="${STATE_DIR:-/var/lib/hackamonth}"
BIN_PATH="${BIN_PATH:-/usr/local/sbin/hackamonth-deploy}"

DOMAIN="${DOMAIN:-hackamonth.io}"
BRANCH="${BRANCH:-main}"

# Safety switches
AUTO_FIREWALL="${AUTO_FIREWALL:-1}"
AUTO_FAIL2BAN="${AUTO_FAIL2BAN:-1}"
AUTO_UNATTENDED_UPGRADES="${AUTO_UNATTENDED_UPGRADES:-1}"
AUTO_SSH_HARDEN="${AUTO_SSH_HARDEN:-1}"

PURGE="${PURGE:-0}"


# Sudo preflight

if ! sudo -n true 2>/dev/null; then
  log "sudo will prompt for your password..."
fi


# Sanity

if [ ! -f /etc/os-release ]; then
  die "Cannot detect OS. /etc/os-release missing."
fi
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] && [ "${ID:-}" != "debian" ]; then
  die "This bootstrap currently supports Ubuntu/Debian. Detected: ${ID:-unknown}"
fi

log "repo=$REPO_URL ref=$REF install_dir=$INSTALL_DIR domain=$DOMAIN branch=$BRANCH"


# Packages

log "Installing baseline packages"
sudo apt-get update -y
sudo apt-get install -y \
  git ca-certificates curl ufw \
  util-linux \
  gnupg


# k3s install

if ! command -v k3s >/dev/null 2>&1; then
  log "Installing k3s"
  curl -sfL https://get.k3s.io | sh -s - \
    --disable=servicelb \
    --write-kubeconfig-mode=644
else
  log "k3s already installed"
fi

# Wait for k3s to be ready
log "Waiting for k3s node to be ready"
for i in $(seq 1 30); do
  if k3s kubectl get nodes | grep -q ' Ready'; then
    break
  fi
  sleep 2
done
k3s kubectl get nodes | grep -q ' Ready' || die "k3s node not ready after 60s"

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Install cert-manager for Let's Encrypt
if ! k3s kubectl get namespace cert-manager >/dev/null 2>&1; then
  log "Installing cert-manager"
  k3s kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
  log "Waiting for cert-manager to be ready"
  k3s kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=120s
fi


# Security

if [ "$AUTO_FIREWALL" = "1" ]; then
  log "Configuring firewall (OpenSSH, 80, 443, 6443)"
  sudo ufw allow OpenSSH >/dev/null || true
  sudo ufw allow 80/tcp >/dev/null || true
  sudo ufw allow 443/tcp >/dev/null || true
  sudo ufw allow 6443/tcp >/dev/null || true  # k3s API server
  sudo ufw --force enable >/dev/null || true
fi

if [ "$AUTO_FAIL2BAN" = "1" ]; then
  log "Installing and enabling fail2ban"
  sudo apt-get install -y fail2ban
  sudo systemctl enable --now fail2ban
fi

if [ "$AUTO_UNATTENDED_UPGRADES" = "1" ]; then
  log "Enabling unattended security upgrades"
  sudo apt-get install -y unattended-upgrades
  sudo dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null || true
fi

if [ "$AUTO_SSH_HARDEN" = "1" ]; then
  log "Hardening SSH (disabling password auth, X11; MaxAuthTries=3)"
  sudo tee /etc/ssh/sshd_config.d/99-hackamonth.conf >/dev/null <<'EOF'
# Applied by hackamonth bootstrap — requires SSH key to be pre-loaded
PasswordAuthentication no
X11Forwarding no
MaxAuthTries 3
EOF
  sudo systemctl reload ssh
fi


# Filesystem

log "Preparing directories"
sudo mkdir -p "$INSTALL_DIR" "$CFG_DIR" "$STATE_DIR"
sudo chmod 0755 "$INSTALL_DIR"
sudo chmod 0750 "$CFG_DIR"
sudo chmod 0700 "$STATE_DIR"
sudo chown -R root:root "$INSTALL_DIR" "$CFG_DIR" "$STATE_DIR"

if [ "$PURGE" = "1" ]; then
  log "PURGE=1 set. Deleting k8s resources and clearing $INSTALL_DIR."
  k3s kubectl delete namespace hackamonth --ignore-not-found || true
  sudo rm -rf "${INSTALL_DIR:?}/"*
fi


# Git

log "Syncing git repo (root-owned checkout)"

if [ -d "$INSTALL_DIR/.git" ]; then
  CURRENT_ORIGIN="$(sudo git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
  if [ "$CURRENT_ORIGIN" != "$REPO_URL" ]; then
    log "Resetting origin from '$CURRENT_ORIGIN' to '$REPO_URL'"
    sudo git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  fi
  sudo git -C "$INSTALL_DIR" fetch --tags --prune origin
else
  sudo git clone "$REPO_URL" "$INSTALL_DIR"
  sudo git -C "$INSTALL_DIR" fetch --tags --prune origin
fi

if [ "$REF" = "main" ] || [ "$REF" = "master" ]; then
  sudo git -C "$INSTALL_DIR" reset --hard "origin/$REF"
else
  sudo git -C "$INSTALL_DIR" checkout -f "$REF"
fi

# Validate required files
sudo test -d "$INSTALL_DIR/k8s" || die "Missing k8s/ directory in $INSTALL_DIR"
sudo test -f "$INSTALL_DIR/deploy/deploy.sh" || die "Missing deploy/deploy.sh in $INSTALL_DIR"
sudo test -f "$INSTALL_DIR/deploy/systemd/hackamonth-deploy.service" || die "Missing systemd service in repo"
sudo test -f "$INSTALL_DIR/deploy/systemd/hackamonth-deploy.timer" || die "Missing systemd timer in repo"


# Install deploy runner

log "Symlinking deploy runner $BIN_PATH -> $INSTALL_DIR/deploy/deploy.sh"
sudo chmod 0755 "$INSTALL_DIR/deploy/deploy.sh"
sudo ln -sf "$INSTALL_DIR/deploy/deploy.sh" "$BIN_PATH"


# Write runtime config

log "Writing runtime config to $CFG_DIR/deploy.env"
sudo tee "$CFG_DIR/deploy.env" >/dev/null <<EOF
INSTALL_DIR=$INSTALL_DIR
STATE_DIR=$STATE_DIR
BRANCH=$BRANCH
DOMAIN=$DOMAIN
KUBECONFIG=/etc/rancher/k3s/k3s.yaml
EOF
sudo chmod 0644 "$CFG_DIR/deploy.env"
sudo chown root:root "$CFG_DIR/deploy.env"

sudo ln -sf "$CFG_DIR/deploy.env" /etc/default/hackamonth-deploy


log "Converging systemd units (authoritative)"

sudo systemctl stop hackamonth-deploy.timer 2>/dev/null || true
sudo systemctl stop hackamonth-deploy.service 2>/dev/null || true
sudo systemctl disable hackamonth-deploy.timer 2>/dev/null || true

sudo rm -f /etc/systemd/system/hackamonth-deploy.service
sudo rm -f /etc/systemd/system/hackamonth-deploy.timer

sudo ln -s "$INSTALL_DIR/deploy/systemd/hackamonth-deploy.service" /etc/systemd/system/hackamonth-deploy.service
sudo ln -s "$INSTALL_DIR/deploy/systemd/hackamonth-deploy.timer"   /etc/systemd/system/hackamonth-deploy.timer

sudo systemctl daemon-reload

sudo systemctl cat hackamonth-deploy.service | grep -q "ExecStart=${BIN_PATH}" \
  || die "Unit mismatch: ExecStart must be ${BIN_PATH}"
sudo systemctl cat hackamonth-deploy.service | grep -q "EnvironmentFile=/etc/default/hackamonth-deploy" \
  || die "Unit mismatch: EnvironmentFile must be /etc/default/hackamonth-deploy"


# Initial deploy + enable reconcile

log "Running initial deploy"
sudo systemctl start hackamonth-deploy.service

log "Enabling periodic reconcile timer"
sudo systemctl enable --now hackamonth-deploy.timer

log "Bootstrap complete"
log "Timer : systemctl status hackamonth-deploy.timer"
log "Logs  : journalctl -u hackamonth-deploy.service -n 200 --no-pager"
log "Cluster: k3s kubectl get all -n hackamonth"
