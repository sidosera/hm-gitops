#!/usr/bin/env bash
set -euo pipefail

log() { printf '%s %s\n' "[hackamonth-deploy]" "$*"; }
die() { printf '%s %s\n' "[hackamonth-deploy][ERROR]" "$*" >&2; exit 1; }

LOCK_FILE="/var/lock/hackamonth-deploy.lock"
STATE_DIR="${STATE_DIR:-/var/lib/hackamonth}"
LAST_GOOD="$STATE_DIR/last_good_sha"

mkdir -p "$STATE_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Another deploy is running, exiting"
  exit 0
fi

: "${INSTALL_DIR:?missing INSTALL_DIR}"
: "${BRANCH:?missing BRANCH}"
: "${DOMAIN:?missing DOMAIN}"
: "${KUBECONFIG:?missing KUBECONFIG}"

export KUBECONFIG

cd "$INSTALL_DIR"

log "Fetching desired state (origin/$BRANCH)"
git fetch --prune origin

CURRENT_SHA="$(git rev-parse HEAD)"
TARGET_SHA="$(git rev-parse "origin/$BRANCH")"

CHANGED=0
if [ "$CURRENT_SHA" != "$TARGET_SHA" ]; then
  log "Updating $CURRENT_SHA -> $TARGET_SHA"
  git reset --hard "origin/$BRANCH"
  CHANGED=1
else
  log "Already at desired revision $CURRENT_SHA"
fi

log "Applying k8s manifests"
k3s kubectl apply -f k8s/base/namespace.yaml
k3s kubectl apply -f k8s/ingress/
k3s kubectl apply -f k8s/base/

# Wait for deployments to roll out
log "Waiting for deployments to be ready"
k3s kubectl rollout status deployment/site -n hackamonth --timeout=120s
k3s kubectl rollout status deployment/getrafty -n hackamonth --timeout=120s

# Health probe: accept 2xx or 3xx as healthy.
# Traefik needs a moment to register routes after start, so retry up to 5 times.
probe() {
  local code
  code=$(curl --max-time 10 -s -o /dev/null -w "%{http_code}" -H "Host: $DOMAIN" "http://127.0.0.1/")
  case "$code" in 2*|3*) return 0 ;; esac
  return 1
}

PROBE_OK=0
for i in 1 2 3 4 5; do
  if probe; then
    PROBE_OK=1
    break
  fi
  log "Health probe attempt $i/5 failed, retrying in 3s..."
  sleep 3
done

if [ "$PROBE_OK" = "1" ]; then
  log "Health probe OK for $DOMAIN"
else
  log "Health probe FAILED after 5 attempts; attempting rollback"
  if [ -f "$LAST_GOOD" ]; then
    GOOD_SHA="$(cat "$LAST_GOOD")"
    log "Rolling back to $GOOD_SHA"
    git reset --hard "$GOOD_SHA"
    k3s kubectl apply -f k8s/base/namespace.yaml
    k3s kubectl apply -f k8s/ingress/
    k3s kubectl apply -f k8s/base/
    k3s kubectl rollout status deployment/site -n hackamonth --timeout=120s
    k3s kubectl rollout status deployment/getrafty -n hackamonth --timeout=120s
    sleep 5
    probe || die "Rollback failed health probe"
    log "Rollback succeeded"
    exit 1
  else
    die "No last_good_sha recorded; cannot rollback"
  fi
fi

echo "$(git rev-parse HEAD)" > "$LAST_GOOD"
log "Recorded last good revision: $(cat "$LAST_GOOD")"

log "Deploy complete"
