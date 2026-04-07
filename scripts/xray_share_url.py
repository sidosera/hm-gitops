#!/usr/bin/env python3
"""Print vless:// share URLs for importing into Xray/V2Ray clients (one per client UUID)."""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Install PyYAML: pip install PyYAML", file=sys.stderr)
    sys.exit(1)


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"Expected mapping in {path}")
    return data


def iter_clients(xray: dict) -> list[tuple[str, str]]:
    """Return list of (uuid, flow) for share links."""
    out: list[tuple[str, str]] = []
    clients = xray.get("vless_clients") or []
    for i, c in enumerate(clients):
        if isinstance(c, dict):
            uid = (c.get("id") or "").strip()
            flow = (c.get("flow") or "").strip()
        else:
            uid = str(c).strip()
            flow = ""
        if not uid:
            raise SystemExit(f"vless_clients[{i}] has empty id")
        out.append((uid, flow))
    legacy = (xray.get("vless_client_id") or "").strip()
    if not out and legacy:
        out.append((legacy, ""))
    if not out:
        raise SystemExit("No secrets.xray.vless_clients or vless_client_id in local-env.yaml")
    return out


def build_vless_url(
    uuid: str,
    host: str,
    port: int,
    path: str,
    flow: str,
    name: str,
) -> str:
    q = {
        "encryption": "none",
        "security": "tls",
        "sni": host,
        "type": "ws",
        "host": host,
        "path": path,
    }
    if flow:
        q["flow"] = flow
    query = urllib.parse.urlencode(q)
    frag = urllib.parse.quote(name, safe="")
    return f"vless://{uuid}@{host}:{port}?{query}#{frag}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (default: parent of scripts/)",
    )
    args = ap.parse_args()
    repo: Path = args.repo
    local_env = repo / "local-env.yaml"
    layout = repo / "ansible" / "controller_layout.yml"
    if not local_env.is_file():
        raise SystemExit(f"Missing {local_env} (copy from local-env.example.yaml)")
    if not layout.is_file():
        raise SystemExit(f"Missing {layout}")

    layout_data = load_yaml(layout)
    host = layout_data.get("hm_xray_public_host")
    if not host or not isinstance(host, str):
        raise SystemExit("ansible/controller_layout.yml: set hm_xray_public_host")

    env = load_yaml(local_env)
    secrets = env.get("secrets")
    if not isinstance(secrets, dict):
        raise SystemExit("local-env.yaml: missing secrets:")
    xray = secrets.get("xray")
    if not isinstance(xray, dict):
        raise SystemExit("local-env.yaml: missing secrets.xray:")

    path = "/xray-ws"
    port = 443
    clients = iter_clients(xray)
    for i, (uid, flow) in enumerate(clients):
        name = f"hm-xray-{i + 1}" if len(clients) > 1 else "hm-xray"
        url = build_vless_url(uid, host.strip(), port, path, flow, name)
        print(url)


if __name__ == "__main__":
    main()
