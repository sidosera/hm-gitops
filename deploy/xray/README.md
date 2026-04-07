# Xray: secrets vs config

- **Secrets** live in **`local-env.yaml`** under `secrets` (see **`vless_clients`** below). Never commit that file. Connection/repo fields for Ansible live under **`secrets.deploy`**. Values are injected when Ansible renders **`deploy/xray/config.yaml.j2`** into **`hm-xray-config`**.
- **Server shape** (ports, path `/xray-ws`, sniffing, etc.) lives in the repo: **`deploy/xray/config.yaml.j2`**. Use **`{% raw %}`** if you ever need literal `{{` in that file.
- **Public hostname** for Traefik is **not** a secret: set **`hm_xray_public_host`** in **`ansible/controller_layout.yml`**. The Xray Ingress is templated from that value.

## VLESS clients (easy multi-user)

Put **one UUID per client** under **`secrets.xray.vless_clients`** (a YAML list). Add a line to add a phone, laptop, or friend:

```yaml
secrets:
  xray:
    vless_clients:
      - "11111111-1111-1111-1111-111111111111"
      - "22222222-2222-2222-2222-222222222222"
```

Optional **per-client flow** (only if you use XTLS flows; leave empty for normal WS+TLS):

```yaml
    vless_clients:
      - id: "uuid-here"
        flow: ""
```

**Legacy:** a single **`secrets.xray.vless_client_id`** string still works if **`vless_clients`** is missing or empty.

Then run **`./hm-playbook.sh -e hm_action=update`**.

## Connection details (all clients)

Use the same values everywhere; only the **UUID** differs per device if you gave each its own entry in **`vless_clients`**.

| Setting | Value |
|--------|--------|
| **Address** | Your public hostname (`hm_xray_public_host` in **`ansible/controller_layout.yml`**, e.g. `proxy.example.com`) |
| **Port** | `443` |
| **Protocol** | VLESS |
| **ID / UUID** | One UUID from **`secrets.xray.vless_clients`** in `local-env.yaml` |
| **Encryption** | `none` (standard for VLESS) |
| **Transport** | WebSocket (WS) |
| **Path** | `/xray-ws` |
| **TLS** | On |
| **SNI** (server name) | Same as **Address** |
| **Host** (WebSocket HTTP Host header) | Same as **Address** (unless your client splits SNI and Host; then match what Traefik expects for that hostname) |

DNS must resolve the **Address** to your VPS; HTTPS certificates are obtained by Traefik for that hostname.

---

## VLESS + REALITY (recommended vs aggressive DPI)

**REALITY** makes the **TLS handshake** toward your VPS look like a connection to a **real third-party site** (the **`dest`**), while the session is actually keyed to your server. Traffic still goes to **your** IP on **port 443**; the **SNI** inside TLS mimics **`server_names`** (e.g. `www.microsoft.com`), not your proxy hostname. Traefik uses **TLS passthrough** (`IngressRouteTCP`) for those SNIs and forwards the raw stream to Xray.

1. Run **`./scripts/xray-reality-keygen.sh`** and copy **PrivateKey** into **`secrets.xray.reality.private_key`** and the **Password / public** line into **`secrets.xray.reality.public_key`** (label varies by Xray version).
2. Set **`dest`** (e.g. `www.microsoft.com:443`) — must be a stable **TLS 1.3** site; **`server_names`** must match names that site presents.
3. Set **`short_ids`**: include **`""`** for the default short id; you may add hex strings.
4. Set **`reality.enabled: true`** in **`local-env.yaml`**, run **`./hm-playbook.sh -e hm_action=update`**.
5. Import URL: **`./scripts/xray-share-url.sh`** (REALITY link; add **`--include-ws`** for the old WebSocket profile).

**Checks:** `kubectl get ingressroutetcp -n hm` shows **`xray-reality`**. Client must support **REALITY** and usually **`xtls-rprx-vision`** on this server.

**Caveats:** Blocking can still happen (your VPS IP, heuristics, client TLS fingerprint, legal/regulatory). **`dest`** must be a site you are allowed to use as a mask; prefer well-known stable TLS endpoints and avoid abusing small sites.

---

### Optional: import URL (`vless://`) — WebSocket path

Many apps can import from the clipboard. Replace **`UUID`** and **`HOST`** (no `https://`):

```text
vless://UUID@HOST:443?encryption=none&security=tls&type=ws&path=%2Fxray-ws&host=HOST&sni=HOST#hm-xray
```

Example: `HOST` = `proxy.example.com`, `UUID` = your lowercase UUID with hyphens.

---

## Android (phone / tablet)

1. Install a VLESS-capable client (common choice: **v2rayNG** from a trusted source).
2. **+** → **Manually enter [VLESS]** (or **Import config from clipboard** if you use the `vless://` link above).
3. **Address** = your hostname, **port** = `443`, **UUID** = yours, **encryption** = none.
4. Under transport: **WebSocket**, **path** = `/xray-ws`, **Host** = same hostname.
5. Enable **TLS**, set **SNI** (or “Peer name”) to the same hostname, enable certificate verification unless you know why to turn it off.
6. Save, select the profile, **connect**. Route “system” or “VPN” per app settings.

---

## iOS (iPhone / iPad)

1. Install a client that supports **VLESS + WebSocket + TLS** (e.g. **Streisand**, **Shadowrocket**, **FoXray** — availability varies by region and App Store policy).
2. Add a **VLESS** server: host, port `443`, UUID, transport **WS**, path `/xray-ws`, **TLS** on, **SNI** = hostname, **Host** = hostname where the UI asks for it.
3. Enable the tunnel / VPN profile when prompted (iOS may require allowing **VPN** in Settings).

---

## Windows (PC)

1. Install **v2rayN** or **Nekoray** (or another Xray-compatible GUI).
2. **Servers** → **Add [VLESS]** (or import `vless://`).
3. Fill **address**, **port** `443`, **UUID**, **transport** WebSocket, **path** `/xray-ws`, **TLS** enabled, **SNI** = hostname.
4. **System proxy** or **TUN** mode depends on the app; start the core and test in a browser.

---

## macOS

1. Use **Nekoray**, **V2rayU**, or another GUI that supports **VLESS + WS + TLS**, or a **Command + Q** menu bar client if you prefer.
2. Create a profile with the same fields as in the table (hostname, `443`, UUID, WS path `/xray-ws`, TLS + SNI).
3. On first connect, allow **network** / **VPN** prompts if macOS asks.

---

## Checks if it fails

- **DNS**: `ping` / `dig` your hostname points to the VPS.
- **Firewall**: VPS allows **80** and **443** (Ansible UFW rules usually do).
- **UUID**: matches **`local-env.yaml`** after **`./hm-playbook.sh -e hm_action=update`**.
- **Typo in path**: must be exactly **`/xray-ws`** (leading slash, lowercase).
