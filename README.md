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
./vm/k3s-bootstrap.sh -e vm_hosts_file=cluster/clusters/hm-cluster/hosts.yml -e hm_action=deploy
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

Kubernetes API TCP 6443 is not a public port. Ansible permits it on server nodes only from inventory node addresses and the pod CIDRs on CNI/WireGuard interfaces. Worker nodes do not expose an API listener. Public TCP defaults are SSH 22, HTTP 80 (server only) and HTTPS/VPN 443. The API port is excluded even if an old public-port profile still lists it.

Apply only this API firewall policy to existing hosts without resetting UFW or restarting K3s:

```bash
ANSIBLE_CONFIG=vm/ansible.cfg ansible-playbook -i vm/inventory/localhost.yml \
  vm/playbooks/api-firewall.yml \
  -e vm_hosts_file="$PWD/cluster/clusters/hm-cluster/hosts.yml" \
  --private-key ~/.ssh/hm-gitops
```

For administration, run kubectl over SSH on the control-plane host, or forward `127.0.0.1:16443` to its `127.0.0.1:6443`. A local kubeconfig using that tunnel must retain CA verification and set `tls-server-name` to the inventory control-plane IP. The Flux Bootstrap workflow uses this tunnel with the shared deploy key and host keys pinned in `vm/ssh_known_hosts`; it does not require public API access. Verify and update pinned SSH host keys when replacing a control-plane machine. Adding a node requires applying the updated inventory firewall policy on existing servers before the node joins.

## GitOps

Bootstrap Flux once:

```bash
KUBECONFIG=/path/to/kubeconfig HM_FLUX_AGE_KEY_FILE=/secure/path/flux-age.agekey ./scripts/flux-bootstrap.sh hm-cluster
```

After that, Flux reconciles:

- `cluster/clusters/hm-cluster/`

## Secrets

Runtime secrets are committed as SOPS-encrypted manifests:

- [.sops.yaml](/Users/sidosera/dist/hackamonth-gitops/.sops.yaml#L1)
- `conf/xray/credentials-secret.sops.yaml` — the canonical Xray Secret `hm/xray-credentials`

Flux decrypts them using the `sops-age` Secret in `flux-system`.

Git is the source of truth for application credentials. The canonical Xray Secret contains four runtime keys: `uk-ldn-1.gateway.json`, `ru-spb-1.gateway.json`, `ru-spb-1.probe.json`, and the retained standby `uk-ldn-1.probe.json`. It includes the existing user UUIDs, service UUIDs, REALITY private keys and authenticated upstream settings. These are ordinary Kubernetes Secret `data` keys, with their complete values encrypted by SOPS in Git. Base64 alone is not encryption. Gateway/probe volumes project only the selected runtime key; applications have no API token or permission to list the rest.

Public routing and probe targets remain in ConfigMaps. There is no database, custom secret controller, init renderer or key generation during deployment. Flux applies the values already committed to Git. Reapplying the same commit neither replaces nor regenerates UUIDs. Adding access is an explicit encrypted Git edit; revocation is an explicit Git edit removing that account. Never commit decrypted config, profile exports or private age identities.

Edit credentials with `SOPS_AGE_KEY_FILE=/secure/path/flux-age.agekey sops conf/xray/credentials-secret.sops.yaml`, validate the complete cluster, then commit/push. SOPS updates its `lastmodified` metadata. A native Kustomize replacement copies that revision into the three Xray workload pod templates, so Flux rolls them without a manual revision bump. Direct live edits are not the source of truth and are reconciled from Git. The consolidation retained all existing values and client profiles.

The credentials exposed by the previous block-string format were replaced on 2 November 2026; profiles from before that rotation are obsolete. The 3 November consolidation does not rotate credentials.

## Secrets encryption and recovery

K3s server provisioning enables `secrets-encryption: true` from first start. Existing single-server clusters use the shared Ansible migration, following [the K3s existing-cluster procedure](https://docs.k3s.io/cli/secrets-encrypt#enable-secrets-encryption-on-an-existing-cluster):

```bash
ANSIBLE_CONFIG=vm/ansible.cfg ansible-playbook -i vm/inventory/localhost.yml \
  vm/playbooks/secrets-encryption.yml \
  -e vm_hosts_file="$PWD/cluster/clusters/hm-cluster/hosts.yml" \
  --private-key ~/.ssh/hm-gitops
```

The migration takes an age-encrypted SQLite/token/config backup, enables encryption, restarts the single control plane, rewrites existing Secrets, verifies the resulting state and backs it up again. Repeated convergence does not rotate keys or regenerate identities. Existing HA clusters are deliberately rejected by this single-server procedure and need the coordinated procedure in the K3s docs. Existing Gateway processes continue running while the control-plane API restarts.

Verify with `sudo k3s secrets-encrypt status`; expect `Enabled`, `reencrypt_finished` and matching hashes. `vm/roles/vm/files/verify-secrets-encryption.py` checks ciphertext prefixes in SQLite without printing Secret payloads. The helper targets only current SQLite records with plaintext predecessor values and adds a resourceVersion-guarded operational annotation (`hm.gitops/storage-encrypted-at`). Data, type and object identity are verified unchanged. This metadata is not a new credential source or a rollout trigger. It then waits for native Kine compaction and performs a bounded online SQLite VACUUM/WAL checkpoint, without another control-plane stop. This can take several minutes; failures leave a resumable marker. An Enabled flag alone does not erase historical copies.

Encrypted recovery archives live under `/var/backups/hm-k3s`, mode 0600 in a root-only directory. Backups are produced through tmpfs and an age pipe so no plaintext archive is written to disk. Keep the private age identity separately, and copy encrypted backups off the server. A backup contains `server/db/state.db`, `server/token`, `config.yaml`, and, once enabled, `server/cred/encryption-config.json`. Restore them together while K3s is stopped, preserving ownership/permissions; the server token and matching encryption configuration are essential. The pre-migration archive restores the pre-encryption state. The encrypted archive requires its matching encryption configuration. Git can rebuild application desired state, but is not a backup of monitoring volumes or Kubernetes runtime state.

An emergency copy of the existing Flux age identity is committed as `conf/bootstrap/flux-age-identity.age`, encrypted with age to the existing administrative `hm-gitops` SSH public key. Its SSH fingerprint is `SHA256:OqrF/Zavic+jHraIVs8za8nOIxixdw5pt1CqgzWVlJs`. Recovery decryption with the matching local private key was verified without writing the plaintext identity to disk. This recovery artifact is not applied as a workload manifest. Keep that SSH private key backed up separately; a repository clone alone cannot decrypt the identity or application Secrets.

To restore the Flux identity into a **new** private file, install age and run:

```bash
umask 077
(set -C; age --decrypt --identity ~/.ssh/hm-gitops \
  conf/bootstrap/flux-age-identity.age > /secure/path/flux-age.agekey)
```

Use that file with `HM_FLUX_AGE_KEY_FILE` during Flux bootstrap. After a complete server loss, a fresh K3s datastore can use a new storage encryption key while Flux restores the same application UUIDs from Git using the recovered age identity. Restoring an old SQLite snapshot instead requires its matching K3s storage encryption key and server token.

The K3s encryption key is stored root-only in `/var/lib/rancher/k3s/server/cred/encryption-config.json`. It protects a copied datastore; it does not protect against an attacker with root access to the running control-plane host. SOPS age private keys and K3s bootstrap keys are recovery roots, not application credentials, and must never be committed in plaintext.

## Current Cluster

Referenced node IPs:

- [hosts.yml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/hm-cluster/hosts.yml#L1)

Cluster-wide Kubernetes:

- [cluster/clusters/hm-cluster/kustomization.yaml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/hm-cluster/kustomization.yaml#L1)

Cluster rollout:

- [cluster/clusters/hm-cluster/kustomization.yaml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/hm-cluster/kustomization.yaml#L1)

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

Export profiles privately (Python with PyYAML and cryptography, plus SOPS, are required):

```sh
SOPS_AGE_KEY_FILE=/secure/path/flux-age.agekey python3 scripts/xray_share_url.py \
  --zone uk-ldn-1 --output-dir /secure/path/proxy-profiles
```

The exporter refuses to write profiles inside this repository, to a directory readable by others, or over existing files/symlinks. Public-key derivation happens in memory, without passing the private key to another process. Imports include IPv4 and IPv6 variants with Chrome, Firefox, and Safari TLS fingerprints, plus the HTTPS backup. Fingerprint variants require client support. The backup requires XHTTP support; “V2Ray” alone does not identify a compatible iOS app or core version. Keep the current installed app until the replacement profile works.

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

- [Services / Overview](https://www.hackamonth.io/status/d/hm-services/services-overview): generic readiness, rolling-hour restarts, working-set memory and CPU. Service and Zone apply to every panel. Instance details are collapsed; there are no VPN or host-capacity panels.
- [VPN / Operations](https://www.hackamonth.io/status/d/hm-vpn/vpn-operations): entry readiness and authenticated client-IP estimates, public-client website connectivity and transport measurements. Entry zone scopes client-facing signals. Probe source zone and entry zone independently scope connectivity checks, including upstream dependencies. Transport traffic is labelled by measurement zone and listener; it is not attributed to the selected entry zone or summed across hops as unique client traffic.
- [Inventory / Capacity](https://www.hackamonth.io/status/d/hm-inventory/inventory-capacity): physical host CPU, available RAM, root-disk capacity and per-interface network traffic, filtered by Zone and Host. The queries work for both virtual and bare-metal hosts.

All zone, service and host options come from metrics. No dashboard contains UK/SPB lists, host names, IPs or regional exporter job names. Missing observations remain missing.

Node-exporter supplies host metrics through read-only host mounts. Kube-state-metrics reads pods in the `hm` namespace, and Prometheus reads this node's kubelet resource metrics. Xray-exporter reads native traffic counters with log parsing disabled. A small poller uses the checksum-verified official Xray CLI to publish aggregate online-IP counts through node-exporter's textfile collector. It has no web UI or history database. Only Xray's read-only StatsService is enabled.

Client counts are lower-bound estimates of distinct source IPs with open authenticated sessions. Shared NAT and Traefik can merge clients, and changing networks can count one device twice. Addresses are deduplicated in memory and never exported as metrics; browsing destinations are not collected. Failed online-statistics reads omit counts and report an unhealthy collector instead of reporting zero clients. Prometheus rates handle counter resets.

Grafana requires authentication. Prometheus and the pod exporters have no public ingress and use NetworkPolicies. Node-exporter uses host networking and is protected by the VPS firewall; port 9100 must remain closed publicly. The Xray API is restricted to its collectors. All monitoring containers have memory limits; Grafana and Prometheus are limited to 320 MiB and 256 MiB respectively.

The existing REALITY route is preserved. A parallel source-address-forwarding listener has been tested, but switching the production route requires separate approval. Until then, client counts remain approximate. The HTTPS fallback and client credentials are unchanged.

Validation:

```sh
python3 -m unittest discover -s tests -v
kubectl kustomize cluster/clusters/hm-cluster >/dev/null
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

`cluster/clusters/hm-cluster/hosts.yml` declares server and worker inventories and pins their K3s version. Flannel uses `wireguard-native` with external node addresses; only peer IPv4 addresses may reach UDP 51820/51821. ServiceLB runs on `machine`, leaving SPB TCP 443 for the gateway. SPB's taint keeps unrelated workloads off its small memory budget. The API server and Git reconciliation are central: a WAN outage interrupts reconciliation, while existing gateway containers continue running. Do not delete/restart healthy pods during a partition.

The SPB gateway runs Xray plus a blackbox-exporter sidecar. `services/gateway/overlays/ru-spb-1/config.json` is an explicit domain suffix allowlist for Russian egress, not a blanket `.ru` exception. Unknown destinations use the original upstream through either REALITY or HTTPS/XHTTP; failure never switches foreign traffic to Russian direct egress. SOCKS probe ports are loopback-only inside the pod; native StatsService is protected by a NetworkPolicy. Node/blackbox metrics use mTLS. TLS private material and authenticated Xray connection settings are SOPS encrypted; public routing settings are ConfigMaps. Certificates must be renewed before November 2027.

Both zones share the three dashboards described above. The VPN dashboard discovers the existing five checks: upstream REALITY, upstream HTTPS backup, local internet control, split-routing default exit, and a Russian site through the split policy. Upstream checks verify the egress IP, not merely TCP connectivity. Alert rules distinguish upstream path failure from local internet/probe failure; an Alertmanager receiver is not configured. This is a Russian datacenter vantage point, not proof of access from every mobile/home ISP.

For client import, use the private `hm-spb-smart-routing.txt` VLESS profile provided outside Git. Existing upstream profiles remain valid. The bootstrap password is unnecessary after key-only SSH is configured.

On small hosts: only source-controller and kustomize-controller are needed by Flux; unused Helm/notification controllers are suspended. Xray/node-exporter/blackbox/online collectors have bounded memory. The node-exporter init container streams its checksum-verified stats-client archive to disk, avoiding an archive-sized RAM allocation. Optional desktop/update GUI daemons are disabled; unattended security upgrades remain enabled. No swap is enabled on the SPB Kubernetes worker.


## Service and placement model

Use [the glossary](GLOSSARY.md) for terminology. `hm-cluster` contains two PoPs, with one zone and one host each:

| PoP | Zone | Host slot | Machine (inventory name / management IP) |
| --- | --- | --- | --- |
| uk-ldn | uk-ldn-1 | uk-ldn-1/1 | 87.106.74.217 |
| ru-spb | ru-spb-1 | ru-spb-1/1 | 212.116.115.29 |

Zone is the scheduling and observability dimension. Country and PoP are descriptive metadata; there is no separate region filter or region scheduling layer. These are logical placement names, not claims of provider-certified AZ isolation or independently verified datacenter geography.

Machine inventory names are IP addresses. Host slots are independent, so replacing a machine does not rename a slot. The existing Kubernetes registration IDs `machine` and `spb-01` remain internal compatibility identifiers for node/pod joins, certificates and volumes; they are not the user-facing machine names. Slash-containing host names are Prometheus labels and Kubernetes annotations; node labels use a label-safe encoded host ID.

Gateway is one service with deployments in both zones. The SPB deployment supplies Russian direct egress and upstream routing; the UK deployment supplies foreign egress. Website and VPN Probe are separate services. Services and VPN filter by cluster and zone; Inventory additionally filters by host slot and displays machine IPs. Service dashboards do not depend on host slot identity.

## Gateway configuration with Kustomize

`services/gateway/base` owns the shared Deployment, Service and ingress NetworkPolicy. `services/gateway/overlays/uk-ldn-1` and `services/gateway/overlays/ru-spb-1` reference that base and supply zone placement, resource budgets, exposed ports, authenticated connection settings and routing. The cluster root references both overlays directly; Flux remains the only workload deployment path.

Each zone-specific `config.json` is a public ConfigMap input. It contains routing, private-address blocks, statistics, observation settings and non-authenticated local diagnostic listeners. `conf/xray/credentials-secret.sops.yaml` contains authenticated VLESS inbounds and, for SPB, its credential-bearing upstream outbounds, as zone-named keys in the shared `xray-credentials` Secret. Those sections stay encrypted together so private keys/client IDs never enter ConfigMaps. Node/metrics TLS Secrets live under shared monitoring configuration; the SPB probe server certificate lives beside its overlay.

The same Xray image starts with `run -confdir /etc/xray` in both zones. A read-only projected volume exposes the selected decrypted Secret key as `00-credentials.json` and the public ConfigMap as `10-config-tail.json`. Xray natively merges the files. The `tail` filename is intentional: direct/blocked outbounds are appended after credential-bearing upstream outbounds, preserving the fail-closed default in SPB. No init renderer or runtime templating process is involved.

Change routing in the zone-specific public JSON and validate with `kubectl kustomize services/gateway/overlays/ZONE` and the routing tests. Kustomize hashes ConfigMaps, so a routing update triggers a rollout. For credential changes, edit the canonical SOPS Secret. The cluster root derives `hm.gitops/credentials-revision` from SOPS metadata and rolls all Xray consumers automatically. Do not override it in individual zone patches. Keep stable Deployment/Service names, client identities, listener ports and Traefik routes during structural refactors. The SPB overlay uses Recreate because its fixed public host port cannot be shared by two pods; UK uses a rolling update.

Validate routing and overlay boundaries with `python -m unittest discover -s tests -v` (Python, PyYAML and kubectl required). The checks cover private-network blocks, fail-closed routing, credential isolation, stable selectors, unchanged source selectors in ingress policies, and shared runtime/image configuration.


## Monitoring identity and fleet growth

Workload identity comes from pod labels, never resource-name regexes. Set `hm.gitops/service` on the pod template when several regional workload names implement one logical service (Gateway and Website use this). Otherwise `app.kubernetes.io/name` is the service identity. Kube-state-metrics exposes only these two allowlisted label keys, alongside the existing small metric set. Recording rules attach the actual node's `cluster` and `zone`, discarding the collector's placement. Memory uses the pod working-set metric so completed init-container samples cannot inflate current usage. The generic dashboard discovers services from these rules, including new application names automatically. Current workload collection is intentionally scoped to namespace `hm`; adding another application namespace requires extending the collector/rule scope and RBAC deliberately.

For any new VM or bare-metal host, register it through the existing inventory/bootstrap and deploy the shared node collector. Register its protected scrape endpoint with stable raw `node` plus canonical `cluster`, `zone`, `host` and `machine` target labels and provision TLS/firewall access. Once scraped, it appears in Inventory automatically; there is no dashboard change. New Kubernetes services follow the label contract above and require no service allowlist or dashboard edit. Existing static scrape targets, network peers and certificates still need explicit host onboarding; metric-discovered filters do not provision machines.

VPN collectors use `service="gateway"`, `zone` for the measurement location, and stable listener names in `target`. Probes use `source_zone`, `target_zone` and `check` independently of exporter location. Add those labels when registering a new path and it appears automatically. Current traffic counters are collected only in UK; other regions are unmeasured until exporters are configured. Client estimates are per entry host and may count one IP in multiple places; they are not unique-user fleet totals.

The dashboard split itself adds no database or controller. The separate VPN Probe service described below adds bounded client/prober processes. Service identity uses one generic recording rule instead of three hardcoded service rules.

Filter regression checks: `python -m unittest discover -s tests -v`; with Prometheus tooling installed, `promtool test rules tests/fixtures/dashboard-filters.test.yml` checks service/region isolation, label precedence, new service discovery, a third region with a differently named bare-metal exporter, memory attribution, unready pods and empty regions against synthetic source metrics.


## Independent VPN Probe service

`services/vpn-probe/base` defines a Deployment, private ClusterIP Service and ingress NetworkPolicy. Only `ru-spb-1` deploys the VPN Probe service. Its pod runs an Xray client and a small Python standard-library probe independently of Gateway. The probe itself schedules both requests, validates responses and exposes `/metrics`; Prometheus only scrapes results. Blackbox Exporter is not used by this service. It has no Kubernetes API token, public metrics port or public SOCKS listener. Prometheus alone can reach its metrics Service; cross-zone pod traffic uses WireGuard. Each container is capped at 40 MiB (80 MiB combined), with a 24 MiB combined memory request. The direct baseline uses the same probe process, adding no container or monitoring database.

The process reads `services/vpn-probe/base/targets.json` (targets, content markers, interval, timeout, loopback SOCKS endpoint and expected exit) from its ConfigMap. Configuration changes roll the pod automatically. It uses a pinned official Python Alpine image, without package installation or a separate build pipeline.

Every minute the SPB observer checks `myanimelist.net`, `youtube.com`, `facebook.com`, `instagram.com` and `x.com` twice: directly through the SPB network (`access="direct"`) and through the UK public TCP 443 VLESS/REALITY listener (`access="vpn"`). There is no UK observer. The SPB pod uses its provider DNS resolvers directly rather than routing DNS through UK CoreDNS. VPN requests use SOCKS5 domain-address requests, leaving destination DNS resolution to Xray, so Russian DNS filtering cannot falsely fail that path. The VPN path tests Russian datacenter access to UK public ingress; it does not test the SPB public listener or represent every residential/mobile ISP.

Both modes require certificate-verified HTTPS, final HTTP 200 and the same recognizable page marker. Known challenge-page titles fail; 403 is a failure. These are landing-page checks, not video playback, login or API tests. Redirects are followed. A separate VPN check verifies the expected UK exit IP through `api.ipify.org`.

The VPN dashboard displays a colored matrix: websites as rows, Direct and VPN as columns. Each cell uses `min_over_time(probe_success[1m])` evaluated now, regardless of the dashboard history range. Any failed check within that minute is red; all successful checks are green. Missing samples, failed service scrapes or observations older than 60 seconds are gray (No data); an explicit -1 display sentinel preserves cells when `up` exists but a usable result does not. Each observation exports its original completion timestamp, so repeated Prometheus scrapes cannot extend its freshness or turn an old failure into a new observation. Configured-target info preserves unknown cells before first completion. HTTP status, exit verification and internal transport diagnostics remain in Details. A red direct / green VPN pair shows restored access; green / red shows a problem specific to the VPN route or egress. Both red needs further investigation; both green shows reachability without demonstrating bypass. No automatic conclusion assumes all HTTP failures are government blocking.

Probe workloads use `hm.gitops/service: vpn-probe`, so they appear independently in the generic Services view. Probe accounts use `probe-e2e@local` and are excluded from end-user client estimates. Credentials are distinct from existing user profiles and relay accounts; each observer receives only the credential for the public entry it tests. They are SOPS encrypted. The account grants normal authenticated VPN egress to that dedicated probe client; it does not grant host or Kubernetes administration.

Dedicated probe accounts and their runtime configs are keys in the same canonical Secret as Gateway. The credential maintenance command is `SOPS_AGE_KEY_FILE=/secure/path/age.key python3 scripts/provision_vpn_probe.py --write`. It preserves existing users, adds/reuses one dedicated account on each Gateway, derives the matching public-listener client configuration, updates the canonical encrypted Secret only when the account/config data changes. An unchanged run is a no-op, and existing users are retained. Kustomize derives workload revisions from SOPS metadata. It makes no cluster writes. Before publishing, render the cluster, run tests, inspect only encrypted changes and verify direct and VPN results from SPB. Keep all referenced runtime keys in the canonical Secret before deploying overlays. Notifications still require an Alertmanager receiver.


Cluster configuration lives in `cluster/clusters/hm-cluster`; Gateway and VPN Probe overlays are named by zone. Bootstrap defaults to `hm-cluster`. Node placement uses `topology.kubernetes.io/zone`, and central services use the `hm.gitops/control-plane` role label. VM or bare-metal machine identity does not change this deployment path. Inventory keeps stable Kubernetes registration IDs explicitly so naming changes do not require draining/rejoining a healthy cluster or regenerating VPN profiles.
