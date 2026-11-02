# Hackamonth GitOps

Minimal GitOps repo for a small k3s cluster.

## Structure

- `vm/`
  Bare-metal only.
- `cluster/base/`
  Shared cluster-wide Kubernetes resources.
- `cluster/clusters/<cluster>/`
  Cluster-wide Kubernetes overlay, Flux objects, referenced node IPs, and service manifests.
- `conf/<app>/`
  App-level source configuration.

## Bare-Metal

Bring up or refresh the hosts and k3s:

```bash
./vm/k3s-bootstrap.sh -e vm_hosts_file=cluster/clusters/edge-01/hosts.yml -e hm_action=deploy
```

This only:

- configures the VPS hosts
- installs or updates k3s
- joins the listed hosts into one k3s cluster

It does not apply workloads.

VM defaults:

- [vm/group_vars/all.yml](/Users/sidosera/dist/hackamonth-gitops/vm/group_vars/all.yml#L1)

The provisioning contract is:

- every host is referenced only by IPv4
- the host must accept SSH on that address
- the GitHub workflow uses the shared gitops SSH key
- the SSH user comes from `vm/group_vars/all.yml`

To add a new VM to a cluster, append its IPv4 to `cluster/clusters/<cluster>/hosts.yml` and run the `Cluster Converge` workflow for that cluster.

## GitOps

Bootstrap Flux once:

```bash
KUBECONFIG=/path/to/kubeconfig HM_FLUX_AGE_KEY_FILE=/secure/path/flux-age.agekey ./scripts/flux-bootstrap.sh edge-01
```

After that, Flux reconciles:

- `cluster/clusters/edge-01/`

## Secrets

Runtime secrets are committed as SOPS-encrypted manifests:

- [.sops.yaml](/Users/sidosera/dist/hackamonth-gitops/.sops.yaml#L1)
- [conf/proxy-service/config.sops.yaml](/Users/sidosera/dist/hackamonth-gitops/conf/proxy-service/config.sops.yaml#L1)

Flux decrypts them using the `sops-age` Secret in `flux-system`.

The proxy Secret contains the final `config.json`. SOPS encrypts the complete `stringData` payload; matching `_secret` names inside a YAML block does not encrypt those nested values. Xray mounts the Secret directly. Never commit decrypted config, profile exports, or age identities.

The credentials exposed by the previous block-string format were replaced on 2 November 2026. Old profiles must be replaced.

## Current Cluster

Referenced node IPs:

- [hosts.yml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/edge-01/hosts.yml#L1)

Cluster-wide Kubernetes:

- [cluster/clusters/edge-01/kustomization.yaml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/edge-01/kustomization.yaml#L1)

Cluster rollout:

- [cluster/clusters/edge-01/kustomization.yaml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/edge-01/kustomization.yaml#L1)

Current public endpoints:

- `https://www.hackamonth.io`
- `87.106.74.217:443` (direct REALITY)


## Proxy connections and recovery

Two independent transports share the existing VPS:

| Profile | Address | TLS name | Transport |
| --- | --- | --- | --- |
| Primary | VPS IPv4 or IPv6, port 443 | `www.google.com` | VLESS RAW / REALITY / Vision |
| Backup | `www.hackamonth.io:443` | `www.hackamonth.io` | VLESS XHTTP / HTTPS / packet-up |

The primary connects directly to the VPS. Do not enable Cloudflare's HTTP proxy for a hostname used as its connection address. Its SNI is the actual REALITY target hostname, which Traefik passes through without terminating TLS. The HTTPS backup uses a separate random path and credential behind Traefik and can traverse Cloudflare's HTTP proxy. The random path is routing configuration, not a substitute for authentication. Both transports still depend on this VPS; an outage or destination-network block can affect both.

The website and backup share the existing HTTPS certificate. ACME uses HTTP-01 on port 80, because TLS-ALPN-01 cannot be passed through Cloudflare's ordinary HTTP proxy. Keep the challenge URL reachable through Cloudflare without a challenge page or access policy.

Export profiles privately (Python with PyYAML, SOPS, and Xray are required):

```sh
SOPS_AGE_KEY_FILE=/secure/path/flux-age.agekey python3 scripts/xray_share_url.py \
  --output-dir /secure/path/proxy-profiles
```

The exporter refuses to write profiles inside this repository. Imports include IPv4 and IPv6 variants with Chrome, Firefox, and Safari TLS fingerprints, plus the HTTPS backup. Fingerprint variants require client support. The backup requires XHTTP support; “V2Ray” alone does not identify a compatible iOS app or core version. Keep the current installed app until the replacement profile works.

Test a profile from the network you need to use:

```sh
python3 scripts/check_proxy.py /secure/path/proxy-profiles/hm-reality-ipv4-chrome.txt
python3 scripts/check_proxy.py /secure/path/proxy-profiles/hm-https-backup.txt
```

The probe binds SOCKS only on loopback, verifies the exit IP and an HTTPS response, downloads 1 MB, and stops its temporary Xray process. It never prints the profile. Tests outside a filtered network do not establish reachability inside it. Compare mobile and Wi-Fi results, timestamps, and errors; change one setting at a time. IPv6 profiles require working IPv6 on the client network.

For future changes, deploy a separate canary Service and ingress route, validate both transports and transfers, then reconcile production and remove the canary. Verify that old credentials fail after rotation. Keep rollback snapshots private and outside Git. Do not restore revoked credentials as a routine rollback; roll back the image while retaining fresh credentials when possible.

Useful read-only server checks:

```sh
sudo kubectl -n hm get pods,svc
sudo kubectl -n flux-system get gitrepositories,kustomizations
sudo kubectl -n hm logs deployment/proxy-service --tail=20
```

The website ownership init container needs only `CHOWN`; all other capabilities remain dropped. Nginx uses the official unprivileged image pinned by digest, listens on 8080 internally, and runs as UID/GID 101 with no capabilities and a read-only root filesystem. Xray runs as a non-root user with all capabilities dropped and a read-only root filesystem.

## Private service metrics dashboard

Open `https://www.hackamonth.io/status/` and sign in with the separate dashboard credentials. The password is handed over privately; Git stores only its SHA-256 credential digest inside a SOPS-encrypted Secret. Proxy credentials cannot be used to sign in.

The page refreshes every 15 seconds and shows:

- active client IP estimates across both authenticated proxy transports;
- proxy upload/download rates and counters since Xray started;
- host CPU, memory working set, root disk usage, uptime, and network rates;
- pod readiness, CPU, memory, and restart counts;
- resource and client-count charts for up to seven days.

Counts come from the version-pinned Xray StatsService online-IP maps. They represent distinct source IPs with open authenticated proxy sessions, rather than the number of devices with a VPN app enabled. Shared NAT can merge devices, and a device changing networks can have more than one address. Client IPs are deduplicated transiently and never returned to the browser or written to history. Browsing destinations are not collected. Missing telemetry is shown as unavailable instead of zero. Counter rates suppress negative spikes after restarts.

The dashboard is a small Python service using the existing metrics-server and read-only kubelet stats, with no host filesystem mounts and no Prometheus/Grafana stack. Its service account can read this node's stats and this namespace's pod metrics; it cannot read Secrets, execute workloads, or use the node proxy. The bundled native Xray stats CLI is downloaded from its pinned official release and verified against a fixed SHA-256 checksum. Only the read-only StatsService is enabled. NetworkPolicies restrict the raw Xray metrics/API ports to the dashboard and restrict proxy traffic to Traefik.

SQLite history uses a 1 GiB local-path claim, with a seven-day retention window. The claim survives dashboard restarts but shares the VPS disk; losing the VPS loses its history. Memory-intensive history requests are avoided by reading at most about 240 time-bucket samples. The main container is non-root, drops all capabilities, uses a read-only root filesystem, and has a 96 MiB memory limit.

To keep source addresses useful, the edge Service uses local external traffic handling, a parallel REALITY listener was tested with PROXY protocol v2, and forwarded HTTP headers are trusted only from Cloudflare's published address ranges. Switching the existing REALITY route to source-address forwarding requires separate approval; until then its client count is a lower-bound estimate. Update those trusted ranges if Cloudflare changes them. Do not expose the unauthenticated native Xray stats ports through an ingress or public NodePort.

Tests:

```sh
python3 -m unittest discover -s tests -v
kubectl kustomize cluster/clusters/edge-01 >/dev/null
```

Changing the proxy configuration does not require changing client profiles unless credentials or public transport settings are also changed. Dashboard enablement preserves the existing proxy credentials.
