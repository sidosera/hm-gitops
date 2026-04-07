#!/usr/bin/env bash
# Generate an X25519 keypair for VLESS REALITY (server privateKey + client publicKey / pbk).
# Paste both into secrets.xray.reality in local-env.yaml.
#
# Requires Docker, or install xray-core and run: xray x25519

set -euo pipefail
IMG="${XRAY_IMAGE:-ghcr.io/xtls/xray-core:26.3.27}"
if command -v docker >/dev/null 2>&1; then
  exec docker run --rm "$IMG" xray x25519
fi
if command -v xray >/dev/null 2>&1; then
  exec xray x25519
fi
echo "Install Docker or xray-core, then re-run." >&2
exit 1
