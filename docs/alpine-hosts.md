# Alpine hosts in the shared fleet

Alpine 3.24+ with OpenRC uses the same `vm/playbooks/bootstrap.yml`, cluster inventory, K3s version, UFW policy and Flux/Kustomize workloads as Ubuntu/Debian. Distribution facts select a small host adapter; there is no second deployment pipeline. No existing machine is automatically reimaged or converted.

## Cluster profile and roles

`cluster/clusters/hm-cluster/profile.yml` is the shared host policy for this cluster. `hosts.yml` only assigns addresses, placement, SSH users, Kubernetes node names and explicit `control_plane` or `agent` roles. Ansible validates role membership before connecting to any host. The same profile is loaded by bootstrap, resource maintenance and CI; a missing or invalid profile fails provisioning rather than silently choosing a host-specific setup.

The common host policy selects cgroup v2, shared root mount propagation, THP mode, the three required kernel modules, sysctls, firewall/SSH settings and disabled optional services. Automatic package updates are forbidden by the profile. The `agent` role contains kubelet memory reservations, eviction thresholds, pod count and conntrack bounds; `control_plane` supplies server resource overrides independently. Join tokens, node identity and networking cannot be overridden through role resource settings. Application container requests/limits, including the online collector's 96 MiB ceiling, remain in native Kubernetes manifests reconciled by Flux.

Bootstrap installs base dependencies, prepares the platform, applies security and the shared kernel/resource policy, writes the selected role's K3s config, and only then installs/starts K3s. For later boots, OpenRC uses its existing `localmount`, `modules`, `sysctl` and `cgroups` services; K3s explicitly requires `localmount`, cgroups and UFW. systemd uses its existing module/sysctl/tmpfiles boot services. THP policy is applied before Kubernetes on both init systems, with no extra daemon or periodic job. Changing policy on an existing host does not reboot it automatically.

## Provisioning

Install the controller requirements, then use the existing bootstrap entry point:

```sh
python3 -m pip install ansible-core==2.20.11
ansible-galaxy collection install -r vm/requirements.yml
./vm/k3s-bootstrap.sh \
  -e vm_hosts_file="$PWD/cluster/clusters/hm-cluster/hosts.yml" \
  --private-key ~/.ssh/hm-gitops
```

A new minimal Alpine image needs a trusted SSH host key and a working administrative SSH key. Set its inventory `user` to the image's existing administrator (typically `root` on a fresh VPS). Python is bootstrapped with `raw` before gathering facts. Password authentication is disabled after key-based access is available. The signed community repository for the installed stable release is enabled for UFW and recovery packages; no edge repository or unverified installer is added.

The running kernel must support cgroup v2 CPU, memory, pids and cpuset controllers, overlayfs, bridge netfilter and WireGuard. The role enables OpenRC cgroups/modules/sysctl at boot and stops before joining if these prerequisites are missing. It does not replace the kernel, change the bootloader or reboot automatically. Alpine's normal virtual-machine kernel is the starting point; a reduced custom kernel must preserve these features.

The pinned K3s installer generates native OpenRC services. UFW's OpenRC service provides `firewall`. The adapter adds explicit `cgroups` and `ufw` requirements in `/etc/conf.d/k3s{,-agent}` so Kubernetes cannot start before its configured firewall; the pinned installer's default dependencies alone are insufficient. The same UFW pre-start hook restores the public NodePort guard. Kubernetes API access remains limited to peers and pod interfaces. Applications, Secret mounts, encrypted UUIDs, metrics and dashboards do not depend on the host's init system.

## Minimal runtime policy

There are no custom scheduled host jobs or automatic package upgrades. The role removes the former Alpine update helper and disables cron/optional services if installed by the provider image. On systemd hosts it disables/masks optional updater/desktop services and timers, disables login-time package checks and removes the old APT memory-throttling drop-in. Existing package transactions are not forcibly killed. Updates are an explicit maintenance operation (`apk upgrade --no-cache` on Alpine, normal apt maintenance on Debian-family hosts); kernel changes get a separately planned reboot.

The baseline installs only tools needed for host administration, firewall and Kubernetes: no host git, GnuPG, Apache tools, unzip or additional wget package. Recovery tools are installed on control-plane hosts only. Fail2ban is opt-in and disabled in the current fleet; UFW SSH rate limiting remains enabled. Core networking, DNS, time synchronization, logging, SSH, UFW and K3s remain. The optional-service lists in the cluster profile can be adjusted if a future host actually requires one of those services (for example multipath storage). Kernel modules explicitly loaded for Alpine are limited to overlay, bridge netfilter and WireGuard, which this cluster uses.

## Validation

```sh
bash tests/provisioning/run-alpine-smoke.sh
python3 -m unittest discover -s tests -v
```

The smoke test uses a digest-pinned Alpine image, disposable SSH keys, loopback-only SSH and no privileged container. It covers Python bootstrap, real apk installation, effective key-only SSH policy, absence of custom update jobs, disabling a stock running cron service, a zero-change second run, checksum-verified K3s binaries and generation of server/agent OpenRC units. It never starts Kubernetes in the container. CI runs the same test without infrastructure credentials.

This is not a VM boot or end-to-end networking test. Before reimaging a live worker, test an Alpine VM through a reboot: verify kernel modules and cgroup controllers, UFW and NodePort protection, K3s node readiness, private metric scrapes and authenticated VPN connectivity. Do not infer a memory saving from the container's process list; measure the same workloads on the VM.

## SPB cutover boundary

Reimaging a host requires a separate cutover. SPB is a worker; UK retains the control plane and monitoring data. Before rebuilding SPB, verify the current encrypted Git state and recovery keys, record its node labels and SSH key, and plan a gateway/probe outage. Verify the replacement host key through the provider console and update `vm/ssh_known_hosts`; never disable host-key checking to get past a rebuild. Reuse its logical zone and node identity with the shared bootstrap. Git restores the same client credentials; no UUID rotation is part of an OS migration. Confirm all pods, direct/VPN probes, firewall rules and reboot persistence before considering the cutover complete. Rolling back the OS is another rebuild, not a git revert.
