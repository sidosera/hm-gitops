#!/usr/bin/env python3
"""Print vless:// share URLs for Xray/V2Ray clients (REALITY when enabled, else WebSocket+TLS)."""

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
    """Return list of (uuid, flow override) for share links; flow may be empty to use default."""
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


def build_vless_ws_url(
    uuid: str,
    host: str,
    port: int,
    path: str,
    flow: str,
    name: str,
) -> str:
    q: dict[str, str] = {
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


def build_vless_reality_url(
    uuid: str,
    connect_host: str,
    port: int,
    pbk: str,
    sni: str,
    sid: str,
    fp: str,
    name: str,
) -> str:
    q: dict[str, str] = {
        "encryption": "none",
        "flow": "xtls-rprx-vision",
        "security": "reality",
        "sni": sni,
        "fp": fp,
        "pbk": pbk,
        "type": "tcp",
        "headerType": "none",
        "sid": sid,
    }
    query = urllib.parse.urlencode(q)
    frag = urllib.parse.quote(name, safe="")
    return f"vless://{uuid}@{connect_host}:{port}?{query}#{frag}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (default: parent of scripts/)",
    )
    ap.add_argument(
        "--ws-only",
        action="store_true",
        help="Print only the WebSocket+TLS URL (ignore REALITY)",
    )
    ap.add_argument(
        "--include-ws",
        action="store_true",
        help="When REALITY is enabled, also print the WebSocket fallback URL",
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
    connect_host = layout_data.get("hm_xray_public_host")
    if not connect_host or not isinstance(connect_host, str):
        raise SystemExit("ansible/controller_layout.yml: set hm_xray_public_host")

    env = load_yaml(local_env)
    secrets = env.get("secrets")
    if not isinstance(secrets, dict):
        raise SystemExit("local-env.yaml: missing secrets:")
    xray = secrets.get("xray")
    if not isinstance(xray, dict):
        raise SystemExit("local-env.yaml: missing secrets.xray:")

    port = 443
    clients = iter_clients(xray)
    reality = xray.get("reality") if isinstance(xray.get("reality"), dict) else {}
    reality_on = bool(reality.get("enabled"))

    if reality_on and not args.ws_only:
        pbk = (reality.get("public_key") or "").strip()
        if not pbk:
            raise SystemExit("secrets.xray.reality.public_key required when reality.enabled")
        names = reality.get("server_names") or []
        if not names:
            raise SystemExit("secrets.xray.reality.server_names required when reality.enabled")
        sni = str(names[0]).strip()
        sids = reality.get("short_ids")
        if not isinstance(sids, list) or not sids:
            raise SystemExit("secrets.xray.reality.short_ids must be a non-empty list")
        sid = sids[0] if sids[0] is not None else ""
        sid = str(sid)
        fp = (reality.get("fingerprint") or "chrome").strip()
        ch = connect_host.strip()
        for i, (uid, _flow) in enumerate(clients):
            name = f"hm-reality-{i + 1}" if len(clients) > 1 else "hm-reality"
            print(build_vless_reality_url(uid, ch, port, pbk, sni, sid, fp, name))

    if (not reality_on) or args.ws_only or (reality_on and args.include_ws):
        path = "/xray-ws"
        for i, (uid, flow) in enumerate(clients):
            name = f"hm-ws-{i + 1}" if len(clients) > 1 else "hm-ws"
            url = build_vless_ws_url(uid, connect_host.strip(), port, path, flow, name)
            print(url)


if __name__ == "__main__":
    main()
