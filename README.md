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
- [services/gateway/overlays/uk/credentials-secret.sops.yaml](services/gateway/overlays/uk/credentials-secret.sops.yaml#L1)

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

Open [Grafana](https://www.hackamonth.io/status/) and sign in with the existing dashboard username and password. The provisioned **Services / Overview** dashboard is the home page. Credentials are stored in a SOPS-encrypted Secret; proxy profiles remain unchanged.

Prometheus scrapes every 30 seconds and stores seven days of history, bounded to 750 MB of blocks on a 2 GiB volume. Grafana has a separate 1 GiB volume for its settings. Both volumes use the VPS disk, so they do not survive loss of the VPS. History starts when Prometheus is deployed; the previous SQLite history volume is retained as an archive and is not imported.

Three purpose-based dashboards share the same Grafana and Prometheus:

- [Services / Overview](https://www.hackamonth.io/status/d/hm-services/services-overview): generic readiness, rolling-hour restarts, working-set memory and CPU. Service and Region apply to every panel. Instance details are collapsed; there are no VPN or host-capacity panels.
- [VPN / Operations](https://www.hackamonth.io/status/d/hm-vpn/vpn-operations): entry readiness and authenticated client-IP estimates, synthetic connectivity paths and transport measurements. Entry region scopes client-facing signals. Probe source and destination independently scope connectivity checks, including upstream dependencies. Transport traffic is labelled by measurement region and listener; it is not attributed to the selected entry region or summed across hops as unique client traffic.
- [Inventory / Capacity](https://www.hackamonth.io/status/d/hm-inventory/inventory-capacity): physical host CPU, available RAM, root-disk capacity and per-interface network traffic, filtered by Region and Host. The queries work for both virtual and bare-metal hosts.

All region, service and host options come from metrics. No dashboard contains UK/SPB lists, host names, IPs or regional exporter job names. Missing observations remain missing.

Node-exporter supplies host metrics through read-only host mounts. Kube-state-metrics reads pods in the `hm` namespace, and Prometheus reads this node's kubelet resource metrics. Xray-exporter reads native traffic counters with log parsing disabled. A small poller uses the checksum-verified official Xray CLI to publish aggregate online-IP counts through node-exporter's textfile collector. It has no web UI or history database. Only Xray's read-only StatsService is enabled.

Client counts are lower-bound estimates of distinct source IPs with open authenticated sessions. Shared NAT and Traefik can merge clients, and changing networks can count one device twice. Addresses are deduplicated in memory and never exported as metrics; browsing destinations are not collected. Failed online-statistics reads omit counts and report an unhealthy collector instead of reporting zero clients. Prometheus rates handle counter resets.

Grafana requires authentication. Prometheus and the pod exporters have no public ingress and use NetworkPolicies. Node-exporter uses host networking and is protected by the VPS firewall; port 9100 must remain closed publicly. The Xray API is restricted to its collectors. All monitoring containers have memory limits; Grafana and Prometheus are limited to 320 MiB and 256 MiB respectively.

The existing REALITY route is preserved. A parallel source-address-forwarding listener has been tested, but switching the production route requires separate approval. Until then, client counts remain approximate. The HTTPS fallback and client credentials are unchanged.

Validation:

```sh
python3 -m unittest discover -s tests -v
kubectl kustomize cluster/clusters/edge-01 >/dev/null
```

## Operating the dashboard

Use Services to investigate a workload, VPN to investigate client connectivity and upstream paths, and Inventory to investigate host capacity. Each starts with all discovered values and can be narrowed with its own filters. Workload readiness is separate from end-to-end connectivity.

- **VPN fails, server checks green:** test the existing client profile from the affected ISP. Compare the authenticated Russia probes with the direct-internet control. They measure datacenter reachability and request duration, but cannot establish availability from every residential/mobile ISP or a client-facing SLO.
- **API failed or pod not ready:** inspect `kubectl -n hm logs deployment/proxy-service` and pod events. Check collector health before interpreting missing data.
- **Client count unknown:** inspect node-exporter and its `online-clients` container. Counts require a successful scrape, successful native API poll and a textfile younger than 60 seconds. A successful observation with no sessions shows zero. Counts remain a lower-bound IP estimate.
- **Memory pressure:** compare MemAvailable with Linux pressure stall information (PSI), swapping and each container's working set / configured memory limit. Occupied swap alone does not prove current pressure.
- **Disk pressure:** inspect both free space and free inodes on `/`. Dashboard thresholds are investigation guides: memory/disk headroom under 20% warns, under 10% is urgent; container memory-limit use above 80% warns, above 95% is urgent. No paging or notifications are configured by the dashboard.

Traffic rates use Grafana's adaptive rate interval with a 30-second scrape interval. Missing samples remain gaps. Recent restarts use a rolling one-hour window and do not count pod replacements.

The layout follows [Google SRE monitoring guidance](https://sre.google/sre-book/monitoring-distributed-systems/) while making unmeasured user-path signals explicit.


## Two-host routing and low-memory deployment

Both hosts use the same Ansible `vm/playbooks/bootstrap.yml` bootstrap and Flux Git/Kustomize deployment. The original `machine` node runs the control plane and central Prometheus/Grafana. `spb-01` (212.116.115.29, 543 MiB) is a tainted K3s worker, without a second control plane or monitoring database. Shared node-exporter/online-client collectors are one DaemonSet on both nodes. Application deployments do not use standalone systemd services.

`cluster/clusters/edge-01/hosts.yml` declares server and worker inventories and pins their K3s version. Flannel uses `wireguard-native` with external node addresses; only peer IPv4 addresses may reach UDP 51820/51821. ServiceLB runs on `machine`, leaving SPB TCP 443 for the gateway. SPB's taint keeps unrelated workloads off its small memory budget. The API server and Git reconciliation are central: a WAN outage interrupts reconciliation, while existing gateway containers continue running. Do not delete/restart healthy pods during a partition.

The SPB gateway runs Xray plus a blackbox-exporter sidecar. `services/gateway/overlays/spb/config.json` is an explicit domain suffix allowlist for Russian egress, not a blanket `.ru` exception. Unknown destinations use the original upstream through either REALITY or HTTPS/XHTTP; failure never switches foreign traffic to Russian direct egress. SOCKS probe ports are loopback-only inside the pod; native StatsService is protected by a NetworkPolicy. Node/blackbox metrics use mTLS. TLS private material and authenticated Xray connection settings are SOPS encrypted; public routing settings are ConfigMaps. Certificates must be renewed before November 2027.

Both regions share the three dashboards described above. The VPN dashboard discovers the existing five checks: upstream REALITY, upstream HTTPS backup, local internet control, split-routing default exit, and a Russian site through the split policy. Upstream checks verify the egress IP, not merely TCP connectivity. Alert rules distinguish upstream path failure from local internet/probe failure; an Alertmanager receiver is not configured. This is a Russian datacenter vantage point, not proof of access from every mobile/home ISP.

For client import, use the private `hm-spb-smart-routing.txt` VLESS profile provided outside Git. Existing upstream profiles remain valid. The bootstrap password is unnecessary after key-only SSH is configured.

On small hosts: only source-controller and kustomize-controller are needed by Flux; unused Helm/notification controllers are suspended. Xray/node-exporter/blackbox/online collectors have bounded memory. The node-exporter init container streams its checksum-verified stats-client archive to disk, avoiding an archive-sized RAM allocation. Optional desktop/update GUI daemons are disabled; unattended security upgrades remain enabled. No swap is enabled on the SPB Kubernetes worker.


## Service model

Use [the glossary](GLOSSARY.md) for infrastructure terminology. **Gateway** is one service with deployments in the `uk` and `spb` regions. Its regional roles differ: SPB provides client ingress, regional egress and upstream routing; UK provides upstream egress and keeps existing client ingress. `proxy-service` and `spb-gateway` are deployment resource names, not different service identities. Website is separate from Gateway. Monitoring workloads such as Grafana and Prometheus are individually selectable services.

Region, provider availability zone and host are separate placement dimensions. `machine` and `spb-01` are hosts; control-plane/worker are cluster roles. Inventory records region and optionally zone. No availability-zone or failover independence is inferred from a region name. `uk` is the existing operational region name, not a new assertion about provider geography.

Prometheus carries `service` and `region` labels. Host/kubelet metrics belong to infrastructure; gateway client metrics retain their Gateway identity even when collected by node-exporter. Services presents the selected service overview first and keeps specialist signals in Details. Inventory covers physical hosts independently; neither dashboard requires a separate monitoring stack. Regional deployment resource names and existing client credentials remain stable.


## Gateway configuration with Kustomize

`services/gateway/base` owns the shared Deployment, Service and ingress NetworkPolicy. `services/gateway/overlays/uk` and `services/gateway/overlays/spb` reference that base and supply regional placement, resource budgets, exposed ports, authenticated connection settings and routing. The cluster root references both overlays directly; Flux remains the only workload deployment path.

Each regional `config.json` is a public ConfigMap input. It contains routing, private-address blocks, statistics, observation settings and non-authenticated local diagnostic listeners. `credentials-secret.sops.yaml` contains authenticated VLESS inbounds and, for SPB, its credential-bearing upstream outbounds. Those sections stay encrypted together so private keys/client IDs never enter ConfigMaps. Node/metrics TLS Secrets live under shared monitoring configuration; the SPB probe server certificate lives beside its overlay.

The same Xray image starts with `run -confdir /etc/xray` in both regions. A read-only projected volume exposes encrypted Secret data as `00-credentials.json` and the public ConfigMap as `10-config-tail.json`. Xray natively merges the files. The `tail` filename is intentional: direct/blocked outbounds are appended after credential-bearing upstream outbounds, preserving the fail-closed default in SPB. No init renderer or runtime templating process is involved.

Change routing in the regional public JSON and validate with `kubectl kustomize services/gateway/overlays/REGION` and the routing tests. Kustomize hashes ConfigMaps, so a routing update triggers a rollout. For credential rotation, edit the SOPS Secret and override/bump `hm.gitops/credentials-revision` in the regional Deployment patch so Xray reloads its credentials. Keep stable Deployment/Service names, client identities, listener ports and Traefik routes during structural refactors. The SPB overlay uses Recreate because its fixed public host port cannot be shared by two pods; UK uses a rolling update.

Validate routing and overlay boundaries with `python -m unittest discover -s tests -v` (Python, PyYAML and kubectl required). The checks cover private-network blocks, fail-closed routing, credential isolation, stable selectors, unchanged source selectors in ingress policies, and shared runtime/image configuration.


## Monitoring identity and fleet growth

Workload identity comes from pod labels, never resource-name regexes. Set `hm.gitops/service` on the pod template when several regional workload names implement one logical service (Gateway and Website use this). Otherwise `app.kubernetes.io/name` is the service identity. Kube-state-metrics exposes only these two allowlisted label keys, alongside the existing small metric set. Recording rules attach the actual node's `region`, discarding the collector's placement. Memory uses the pod working-set metric so completed init-container samples cannot inflate current usage. The generic dashboard discovers services from these rules, including new application names automatically. Current workload collection is intentionally scoped to namespace `hm`; adding another application namespace requires extending the collector/rule scope and RBAC deliberately.

For any new VM or bare-metal host, register it through the existing inventory/bootstrap and deploy the shared node collector. Register its protected scrape endpoint with stable `node` and `region` target labels and provision TLS/firewall access. Once scraped, it appears in Inventory automatically; there is no dashboard change. New Kubernetes services follow the label contract above and require no service allowlist or dashboard edit. Existing static scrape targets, network peers and certificates still need explicit host onboarding; metric-discovered filters do not provision machines.

VPN collectors use `service="gateway"`, `region` for the measurement location, and stable listener names in `target`. Probes use `source_region`, `target_region` and `check` independently of exporter location. Add those labels when registering a new path and it appears automatically. Current traffic counters are collected only in UK; other regions are unmeasured until exporters are configured. Client estimates are per entry host and may count one IP in multiple places; they are not unique-user fleet totals.

This split adds no monitoring process, database or controller. It adds a Grafana JSON document and one allowlisted kube-state-metrics family; the identity rules replace three hardcoded service rules with one generic rule.

Filter regression checks: `python -m unittest discover -s tests -v`; with Prometheus tooling installed, `promtool test rules tests/fixtures/dashboard-filters.test.yml` checks service/region isolation, label precedence, new service discovery, a third region with a differently named bare-metal exporter, memory attribution, unready pods and empty regions against synthetic source metrics.
