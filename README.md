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

## Grafana and Prometheus

Open [Grafana](https://www.hackamonth.io/status/) and sign in with the existing dashboard username and password. The provisioned **VPS & Proxy** dashboard is the home page. Credentials are stored in a SOPS-encrypted Secret; proxy profiles remain unchanged.

Prometheus scrapes every 30 seconds and stores seven days of history, bounded to 750 MB of blocks on a 2 GiB volume. Grafana has a separate 1 GiB volume for its settings. Both volumes use the VPS disk, so they do not survive loss of the VPS. History starts when Prometheus is deployed; the previous SQLite history volume is retained as an archive and is not imported.

The dashboard shows:

- estimated active client IPs and online-statistics health;
- proxy upload/download rates and cumulative traffic;
- host CPU, available memory, root disk use, uptime, and network traffic;
- pod readiness, CPU, memory, and restarts;
- monitoring memory usage and scrape-target health.

Node-exporter supplies host metrics through read-only host mounts. Kube-state-metrics reads pods in the `hm` namespace, and Prometheus reads this node's kubelet resource metrics. Xray-exporter reads native traffic counters with log parsing disabled. A small poller uses the checksum-verified official Xray CLI to publish aggregate online-IP counts through node-exporter's textfile collector. It has no web UI or history database. Only Xray's read-only StatsService is enabled.

Client counts are lower-bound estimates of distinct source IPs with open authenticated sessions. Shared NAT and Traefik can merge clients, and changing networks can count one device twice. Addresses are deduplicated in memory and never exported as metrics; browsing destinations are not collected. Failed online-statistics reads omit counts and report an unhealthy collector instead of reporting zero clients. Prometheus rates handle counter resets.

Grafana requires authentication. Prometheus and the pod exporters have no public ingress and use NetworkPolicies. Node-exporter uses host networking and is protected by the VPS firewall; port 9100 must remain closed publicly. The Xray API is restricted to its collectors. All monitoring containers have memory limits; Grafana and Prometheus are limited to 320 MiB and 256 MiB respectively.

The existing REALITY route is preserved. A parallel source-address-forwarding listener has been tested, but switching the production route requires separate approval. Until then, client counts remain approximate. The HTTPS fallback and client credentials are unchanged.

Validation:

```sh
python3 -m unittest discover -s tests -v
kubectl kustomize cluster/clusters/edge-01 >/dev/null
```
