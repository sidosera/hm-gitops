#!/usr/bin/env bash
# Install Flux controllers into a cluster, publish the SOPS age key for decryption,
# and apply this repository's Flux source + Kustomization objects.
#
# Usage:
#   KUBECONFIG=/path/to/kubeconfig ./scripts/flux-bootstrap.sh [cluster]
#   HM_FLUX_AGE_KEY_FILE=/secure/path/flux-age.agekey KUBECONFIG=/path/to/kubeconfig ./scripts/flux-bootstrap.sh edge-01

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER="${1:-edge-01}"
CLUSTER_DIR="$ROOT/cluster/clusters/$CLUSTER"
FLUX_SYSTEM_DIR="$CLUSTER_DIR/flux-system"
AGE_KEY_FILE="${HM_FLUX_AGE_KEY_FILE:-$ROOT/.secrets/flux-age.agekey}"

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "Set KUBECONFIG to the target cluster kubeconfig." >&2
  exit 1
fi
if [[ ! -d "$FLUX_SYSTEM_DIR" ]]; then
  echo "Missing Flux system directory: $FLUX_SYSTEM_DIR" >&2
  exit 1
fi
if [[ ! -f "$AGE_KEY_FILE" ]]; then
  echo "Missing age key file: $AGE_KEY_FILE" >&2
  exit 1
fi
if ! command -v kubectl >/dev/null 2>&1; then
  echo "Missing kubectl in PATH." >&2
  exit 1
fi
if ! command -v flux >/dev/null 2>&1; then
  echo "Missing flux in PATH." >&2
  exit 1
fi

kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f -
flux install
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey="$AGE_KEY_FILE" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k "$FLUX_SYSTEM_DIR"
