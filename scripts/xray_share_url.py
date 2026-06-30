#!/usr/bin/env python3
"""Print vless:// REALITY share URLs for importing into Xray/V2Ray clients."""

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


def build_vless_reality_url(uuid: str, host: str, port: int, reality: dict, name: str) -> str:
    sni = reality.get("server_name") or reality.get("sni") or "hackamonth.io"
    q = {
        "encryption": "none",
        "security": "reality",
        "sni": sni,
        "fp": reality.get("fingerprint", "chrome"),
        "pbk": reality["public_key"],
        "sid": reality["short_ids"][0],
        "type": "tcp",
        "flow": "xtls-rprx-vision",
    }
    # IPv6 addresses need brackets in the host part of the URL
    if ":" in host:
        authority = f"[{host}]:{port}"
    else:
        authority = f"{host}:{port}"
    query = urllib.parse.urlencode(q)
    frag = urllib.parse.quote(name, safe="")
    return f"vless://{uuid}@{authority}?{query}#{frag}"


def _find_node_ipv6(repo: Path) -> str:
    """Return vm_node_ipv6 from the first hosts.yml found under cluster/clusters/."""
    for hosts_file in sorted((repo / "cluster" / "clusters").rglob("hosts.yml")):
        data = load_yaml(hosts_file)
        ipv6 = data.get("vm_node_ipv6", "")
        if ipv6:
            return ipv6
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--host", help="Override server host/IP (default: vm_node_ipv6 from hosts.yml)")
    ap.add_argument("--port", type=int, default=443)
    args = ap.parse_args()

    repo: Path = args.repo
    local_env_path = repo / "local-env.yaml"
    if not local_env_path.is_file():
        raise SystemExit(f"Missing {local_env_path} (copy from local-env.example.yaml)")

    env = load_yaml(local_env_path)
    secrets = env.get("secrets", {})
    xray = secrets.get("xray", {})
    reality = xray.get("reality", {})

    if not reality.get("public_key"):
        raise SystemExit("local-env.yaml: missing secrets.xray.reality.public_key")
    if not reality.get("short_ids"):
        raise SystemExit("local-env.yaml: missing secrets.xray.reality.short_ids")

    clients = xray.get("vless_clients") or []
    if not clients:
        raise SystemExit("local-env.yaml: missing secrets.xray.vless_clients")

    host = args.host or env.get("xray_host") or _find_node_ipv6(repo) or "jump.hackamonth.io"

    for i, client in enumerate(clients):
        uuid = client if isinstance(client, str) else client.get("id", "")
        name = f"hm-proxy-{i + 1}" if len(clients) > 1 else "hm-proxy"
        url = build_vless_reality_url(uuid, host, args.port, reality, name)
        print(url)


if __name__ == "__main__":
    main()
