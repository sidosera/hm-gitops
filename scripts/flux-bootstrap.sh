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
# Only the controllers required for Git + Kustomize reconciliation on this small host.
# Opt in explicitly if HelmRelease or notification resources are introduced later.
FLUX_COMPONENTS="${HM_FLUX_COMPONENTS:-source-controller,kustomize-controller}"
flux install --components="$FLUX_COMPONENTS"
for controller in helm-controller notification-controller; do
  if [[ ",$FLUX_COMPONENTS," != *",$controller,"* ]]; then
    if [[ "$controller" == helm-controller ]]; then
      resources="helmreleases.helm.toolkit.fluxcd.io"
    else
      resources="alerts.notification.toolkit.fluxcd.io,providers.notification.toolkit.fluxcd.io,receivers.notification.toolkit.fluxcd.io"
    fi
    if [[ -n "$(kubectl get "$resources" -A --no-headers 2>/dev/null)" ]]; then
      echo "Refusing to suspend $controller: managed resources still exist." >&2
      exit 1
    fi
    if kubectl -n flux-system get deployment "$controller" >/dev/null 2>&1; then
      kubectl -n flux-system scale deployment "$controller" --replicas=0
    fi
  fi
done
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey="$AGE_KEY_FILE" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k "$FLUX_SYSTEM_DIR"
