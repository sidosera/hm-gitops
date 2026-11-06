# HM GitOps

One K3s cluster managed through Ansible, Flux and encrypted Git configuration. Git is the source of truth; merged changes reconcile through Flux.

## Architecture

- **Cluster:** shared deployment/control-plane boundary (`hm-cluster`).
- **Zone:** placement and observability boundary (`uk-ldn-1`, `ru-spb-1`). PoP/country describe location; they do not imply independent failure domains.
- **Host:** stable capacity slot, such as `ru-spb-1/1`. A **machine** occupies that slot and is named by its management IP in inventory.
- **Service:** application capability independent of hosts. Gateway has deployments in both zones; VPN Probe is a separate service.

The control-plane host runs central Flux, Prometheus and Grafana. Agents use the same provisioning/deployment pipeline with a smaller resource profile. Applications use Kubernetes Service DNS; replacing a machine preserves its logical slot, private addresses and service configuration.

| Source | Responsibility |
| --- | --- |
| [hosts.yml](cluster/clusters/hm-cluster/hosts.yml) | Machines, roles, placement and private addresses |
| [profile.yml](cluster/clusters/hm-cluster/profile.yml) | Shared host policy, K3s version and role resource budgets |
| [vm/](vm/) | Ansible provisioning and OS adapters |
| [cluster/clusters/](cluster/clusters/) | Cluster overlays and Flux reconciliation |
| [services/](services/) | Kubernetes service bases and zone overlays |
| [conf/](conf/) | Application configuration and encrypted Secrets |
| [cmd/hmctl/](cmd/hmctl/), [internal/](internal/) | CLI entry point, shared GitOps code, cluster and service handlers |

## Operations

### Setup

Download the `hmctl` artifact from GitHub Actions and verify `SHA256SUMS`, or build locally with Go 1.27.3+:

```sh
make build
export PATH="$PWD/bin:$PATH"
export SOPS_AGE_KEY_FILE=/secure/path/flux-age.agekey
hmctl --help
```

Run from the repository, or pass `--repo /path/to/hm-gitops`. The age identity must be owned by you with mode `0600`. PR creation requires Git and authenticated `gh`; only profile checks require Xray in `PATH`.

### Change workflow

Start each configuration change from a clean, current `main`:

```sh
git switch main
git pull --ff-only
```

Mutations open a PR; credential changes stay encrypted. Review and merge it, then wait for Flux before using the change. The CLI leaves you on the PR branch; return to `main` before the next change.

| Option | Use |
| --- | --- |
| `--dry-run` | Validate without writing or publishing. |
| `--local-only` | Write configuration locally without a commit or PR. |
| `--zone ru-spb-1` | Restrict gateway account changes; default is all zones. |
| `--cluster hm-cluster` | Select inventory; place before `cluster` commands. |
| `--json` | Return metadata without UUIDs or connection URLs. |

`--dry-run` and `--local-only` cannot combine. The `--` separator after the service name is optional.

### Add, list or revoke an account

```sh
hmctl service gateway --add-account --label alice
hmctl service gateway --list-accounts --json
hmctl service gateway --remove-account --label alice
```

Labels may be short names or email addresses. `probe-e2e@service-account` is an ordinary label convention. Adding an existing label reuses its UUID; revocation defaults to all zones.

### Issue or update a client profile

After the account PR is deployed:

```sh
hmctl service gateway --export-account --label alice --zone ru-spb-1 \
  --output-dir /secure/path/alice

# Reissue for a different connection address; keep the UUID.
hmctl service gateway --export-account --label alice --zone ru-spb-1 \
  --endpoint vpn.example.com --output-dir /secure/path/alice-updated
```

Use a private directory outside the repository. Files are mode `0600` and never overwritten. Send the exported `.txt` profiles privately; the user imports their VLESS URLs into the client.

`--endpoint` changes exported REALITY profiles only; the HTTPS backup hostname uses `--https-host`. It does not provision machines, update DNS or change profiles already imported by users. An IP-literal profile needs reimporting after an address change; a stable DNS-only name can keep working if credentials and server settings remain unchanged.

### Check connectivity or configure the probe

```sh
hmctl service gateway --check-profile /secure/path/alice/hm-reality-ipv4-chrome.txt

hmctl service vpn-probe --sync-credentials \
  --client-id-file /secure/path/probe-client.uuid
```

Profile checks verify exit IP, HTTPS and a transfer from the machine running the command. Use `--expected-exit-ip NEW_EXIT_IP` when the expected exit differs from the control-plane inventory address.

The probe UUID file must contain one existing UUID authorized on each gateway referenced by the probes, and have mode `0600`. Create access through the normal gateway account workflow first. Probe sync updates encrypted runtime configuration through a PR; it never creates accounts or changes labels.

### Replace a machine

Verify its SSH host key independently through the provider console, then prepare the inventory PR:

```sh
hmctl cluster host replace ru-spb-1/1 --ip NEW_PUBLIC_IPV4 \
  --ssh-host-key 'ssh-ed25519 VERIFIED_PUBLIC_HOST_KEY'
```

Logical host identity, private addresses and service credentials stay unchanged. Supply `--ipv6 NEW_PUBLIC_IPV6` to retain IPv6; otherwise the previous public IPv6 is removed.

After merging, run [host convergence](#provisioning) from `main`.

Verify service health before moving DNS or distributing updated profiles. Replacing the sole control plane also requires datastore recovery or an explicit cluster rebuild/rejoin; an inventory PR alone is insufficient.

### Inspect deployment health

Run on the control-plane host over SSH:

```sh
sudo kubectl -n flux-system get gitrepositories,kustomizations
sudo kubectl -n hm get pods,svc
sudo kubectl -n hm logs deployment/proxy-service --tail=20
```

Keep private identities and exported profiles outside Git. Do not restore revoked credentials when rolling back an application change.

## Provisioning

Add a machine to `hosts.yml` with an explicit `control_plane` or `agent` role. Verify its SSH host key through the provider console and pin it in [vm/ssh_known_hosts](vm/ssh_known_hosts). Configure the administrative SSH user/key before disabling password access.

Converge hosts from the merged configuration:

```sh
gh workflow run cluster-converge.yml --ref main -f cluster_name=hm-cluster
```

Or run locally with the Ansible requirements installed:

```sh
python3 -m pip install ansible-core==2.20.11 PyYAML==6.0.3
ansible-galaxy collection install -r vm/requirements.yml
./vm/k3s-bootstrap.sh \
  -e vm_hosts_file="$PWD/cluster/clusters/hm-cluster/hosts.yml" \
  -e hm_action=deploy --private-key ~/.ssh/hm-gitops
```

Convergence configures hosts and installs/joins K3s; Flux deploys workloads. Bootstrap Flux once with a CA-verified kubeconfig reached through SSH:

```sh
KUBECONFIG=/path/to/kubeconfig \
HM_FLUX_AGE_KEY_FILE=/secure/path/flux-age.agekey \
  ./scripts/flux-bootstrap.sh hm-cluster
```

The Flux Bootstrap workflow retrieves kubeconfig over pinned SSH and uses a localhost tunnel. Manual administration can run kubectl over SSH or tunnel local port 16443 to control-plane `127.0.0.1:6443`; retain CA verification and set kubeconfig `tls-server-name` to the inventory control-plane IP. No public Kubernetes API is required.

### Alpine and small hosts

Alpine/OpenRC and Ubuntu/Debian/systemd share the same inventory, role profile and Flux workloads. Minimal Alpine images get Python through Ansible `raw` before fact gathering. The kernel must support cgroup v2 CPU/memory/pids/cpuset, overlayfs, bridge netfilter and WireGuard. Bootstrap checks prerequisites and starts the private network, firewall and cgroups before K3s.

The shared profile controls kubelet reservations/evictions, pod limits, sysctls and required modules. Optional daemons, cron and automatic package updates are disabled; upgrades and reboots are explicit maintenance. Central monitoring stays off small agents. Reimaging is a planned outage: verify replacement SSH trust, reuse logical identity, then check node readiness, services, probes and reboot persistence. Git retains client UUIDs; OS rollback requires rebuilding the machine.

## Services and networking

Gateway's Russian deployment sends `.ru` domains (including subdomains) and configured exceptions through Russian egress. Other destinations use the private relay `proxy-service.hm.svc.cluster.local:10443` for UK egress. Relay failure does not fall back to Russian direct egress. Edit routing in [zone overlays](services/gateway/overlays/), not client profiles.

Hosts communicate over the bootstrap-managed WireGuard mesh `hm0` (peer-restricted UDP 51822). K3s uses private node addresses; Flannel VXLAN runs inside this encrypted mesh. Public peer addresses belong to transport inventory; applications discover private Services through Kubernetes DNS. Public-entry discovery intentionally resolves to public listener addresses for end-to-end probes.

| Client transport | Connection |
| --- | --- |
| VLESS REALITY / Vision | Direct machine IP or DNS-only hostname, TCP 443 |
| VLESS XHTTP / HTTPS | Configured HTTPS hostname, TCP 443; supports Cloudflare's HTTP proxy |

Use the exported profiles for actual addresses, SNI and keys. Keep REALITY connection hostnames DNS-only. HTTPS backup settings use `--https-host`. Grafana is served at `/status/`; unmatched `/` requests return 404. HTTP 80 remains available for ACME HTTP-01 renewal.

Gateway overlays combine public ConfigMap routing with the selected key from `hm/xray-credentials`. Xray merges the projected files natively. ConfigMap hashes and the SOPS revision trigger rollouts; no runtime renderer or credential generation is involved.

### Network changes

Before changing an existing cluster's transport, retain SSH/provider-console recovery and an encrypted datastore/config backup. Run bootstrap with `-e hm_prepare_network_only=true`, verify bidirectional private IPv4/IPv6, then run full convergence. K3s restarts may interrupt service. Verify node readiness, private API/metrics, public VPN entry and public management-port denial before reconciling dependent workloads.

If private connectivity fails, restore the previous host network/firewall/K3s configuration through SSH or the console, restart server then agents, and restore the previous Flux revision. Retain current authorized credentials when rolling back application code.

## Monitoring

Open [Grafana](https://www.hackamonth.io/status/) with the existing private credentials. The **HM** folder contains:

- **Hosts:** discovered host availability, CPU execution, available RAM and network traffic. Down hosts remain visible while their Node exists; missing resource samples remain gaps. CPU excludes I/O wait/steal; RAM uses `MemAvailable`. RX is positive, TX negative.
- **Proxy:** direct/VPN connectivity history, gateway and egress traffic, and the top 100 client labels. Gateway and egress are different measurement points; do not sum them as unique traffic. Per-client byte counters include probes and exclude the internal relay. HTTP request/session counts are not measured.

Filters use cluster/zone/host metadata rather than fixed machine addresses. Before scaling gateway replicas, give each exporter a distinct instance target; scraping a load-balanced Service does not aggregate independent counters correctly.

VPN Probe runs in `ru-spb-1`. Each minute it requests configured sites directly and through an authenticated public VPN entry. Targets and response checks live in [targets.json](services/vpn-probe/base/targets.json). The chart uses the minimum result per minute: 0 is failed, 1 is successful, missing/stale samples are gaps. Red direct / green VPN demonstrates restored reachability at that observer; a datacenter probe does not represent every mobile/home ISP.

Prometheus keeps seven days of history with a 750 MB block limit. Monitoring volumes are local and require separate recovery after disk loss. Alerts need an Alertmanager receiver. Metrics and StatsService remain private; node-exporter uses mTLS and read-only Kubernetes node discovery supplies host identity.

## Security and recovery

The repository is public: addresses, topology and encrypted metadata are visible. UUIDs are bearer credentials; anyone with a valid profile can use it until revoked. Git stores credentials only as SOPS ciphertext; Flux decrypts into Kubernetes Secrets and K3s encrypts Secret records at rest. Public age recipients cannot decrypt them. The canonical [Xray Secret](conf/xray/credentials-secret.sops.yaml) holds gateway/probe configuration; routing and probe targets remain public ConfigMaps.

- Protect GitHub write access, deployment workflows, administrative SSH keys and the Flux age identity as cluster-administrator access. Encryption does not protect a compromised running host or an authorized administrator.
- The shared Xray Secret is not zone isolation. A node serving a consumer can obtain the complete Secret object; investigate and rotate shared credentials after a host-root compromise.
- Application containers run non-root with capabilities dropped, read-only filesystems and no Kubernetes API tokens. Prometheus has read-only node discovery; node-exporter's host mounts/networking remain a privileged trust boundary.
- Public exposure is SSH 22 and required ingress/VPN 80/443. API/exporter ports stay private. Ansible's raw public-interface NodePort guard runs before kube-proxy; UFW status alone does not prove closure. Recheck after interface or port-range changes.
- Private/reserved destinations and host management endpoints are denied through VPN egress, including after DNS resolution. SSH trust is pinned; never disable verification to bypass a reimage mismatch.
- Keep private identities, profiles and backups outside Git. CI scans reachable history with redacted Gitleaks output; ignore entries apply only to reviewed historical fingerprints. Deleting a leaked credential does not revoke it or remove public copies.

Earlier audits (3–4 November 2026) checked selected active credentials against reachable Git history and targeted external IPv4 TCP endpoints. They did not cover all ports, IPv6/UDP, forks/caches, or prove absence of compromise. Revalidate the deployed revision after provisioning or exposure changes.

### Recover the Flux age identity

[conf/bootstrap/flux-age-identity.age](conf/bootstrap/flux-age-identity.age) is encrypted to the administrative SSH key. A repository clone alone cannot decrypt it. With age installed and the matching private key, restore into a new private file:

```sh
umask 077
(set -C; age --decrypt --identity ~/.ssh/hm-gitops \
  conf/bootstrap/flux-age-identity.age > /secure/path/flux-age.agekey)
```

Use this file with `SOPS_AGE_KEY_FILE` for `hmctl` or `HM_FLUX_AGE_KEY_FILE` for Flux bootstrap. Back up the administrative SSH key separately. The Kubernetes `flux-system/sops-age` Secret contains the decrypted identity; it is never stored as plaintext in Git.

### Datastore encryption and recovery

New control planes enable K3s Secret encryption from first start. For an existing single-server cluster, use the shared migration:

```sh
ANSIBLE_CONFIG=vm/ansible.cfg ansible-playbook -i vm/inventory/localhost.yml \
  vm/playbooks/secrets-encryption.yml \
  -e vm_hosts_file="$PWD/cluster/clusters/hm-cluster/hosts.yml" \
  --private-key ~/.ssh/hm-gitops
```

This backs up, enables encryption, restarts the control plane, rewrites existing Secrets and verifies stored ciphertext. It rejects HA clusters, which require coordinated migration. Check `sudo k3s secrets-encrypt status` for `Enabled`, `reencrypt_finished` and matching hashes.

Encrypted archives in `/var/backups/hm-k3s` contain `server/db/state.db`, `server/token`, `config.yaml` and the matching `server/cred/encryption-config.json`. Copy them off-host. Restore these together while K3s is stopped, preserving ownership and permissions. Git reconstructs desired application state, not Kubernetes runtime state or monitoring volumes. A fresh datastore can use a new storage key while recovering the same application UUIDs through Flux.

## Validation

Run relevant checks before merging; CI runs without infrastructure credentials:

```sh
make fmt-check test vet build
python3 -m unittest discover -s tests -v
kubectl kustomize cluster/clusters/hm-cluster >/dev/null
promtool test rules tests/fixtures/*.test.yml
```

For provisioning changes, also run `bash tests/provisioning/run-alpine-smoke.sh`. It checks the Alpine adapter in a container; validate VM reboot, private networking and service health separately before reimaging a live host.
