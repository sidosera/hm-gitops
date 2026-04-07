#!/usr/bin/env bash
# Print vless:// URLs for your Xray clients (reads local-env.yaml + ansible/controller_layout.yml).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/xray_share_url.py" --repo "$ROOT" "$@"
