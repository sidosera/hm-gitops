# Security boundaries

This repository is public. Addresses, topology, service names, routing rules and encrypted Secret metadata are public information. Server invisibility is not a security boundary. Client credentials, REALITY private keys, TLS private keys, SSH private keys and the SOPS decryption identity must remain private.

Git stores application secrets only as SOPS ciphertext. Flux decrypts them into Kubernetes Secrets; K3s encrypts Secret records in its SQLite datastore. The age recovery artifact is encrypted to the administrator's SSH key. Copying Git, knowing the public age recipient, or generating a UUID does not authorize a client: its UUID must already be allowed by the gateway. Client UUIDs are bearer credentials: anyone receiving a valid profile can use it until revoked. Export profiles only to an owner-only directory outside Git; existing files and symlinks are refused. Public-key derivation never puts a private key in a subprocess argument.

## Administrative trust

Write access to deployed Git configuration, the privileged deployment workflow, a host's root account or the Flux decryption identity is administrative access. SOPS and datastore encryption do not protect against those administrators or a running host compromise. Protect the GitHub account and SSH key accordingly. The current shared SSH deployment identity has privileged host access and can also decrypt the recovery artifact; it must be treated as a cluster administrator credential.

The unified `xray-credentials` Secret is a deliberate administrative convenience, not isolation between zones. Containers receive only their selected key through projected volumes, but a node serving a consumer can obtain the whole Secret object. Compromise of a worker's root account must therefore trigger investigation and rotation of the shared Xray credentials, not just that worker's projected profile. No claim of tenant isolation is made for this shared cluster.

## Runtime and network

Runtime application containers run without root, privilege escalation, capabilities or writable root filesystems. The legacy website build init containers are an exception described below. Application API tokens are disabled; Prometheus can only read `nodes/metrics` for inventoried nodes and cannot read Secrets. Kube-state-metrics, its API watches and RBAC have been removed. Probe Python runs in isolated mode (`-I`) from a read-only ConfigMap; native Xray counters need no CLI poller or executable download. There is no application systemd timer or public command-execution endpoint. A Python scheduling loop is not by itself a remote code execution boundary.

Node-exporter retains read-only host filesystem/proc/sys mounts and host networking to measure the host, but has no host PID namespace, root privileges or Kubernetes token. Those mounts remain a larger attack surface than ordinary application pods. Changing the implementation language would not remove that access.

Only SSH and required ingress/VPN ports are public. The Kubernetes API and exporter ports are restricted to cluster peers. **UFW status alone is insufficient**: kube-proxy installs ACCEPT rules ahead of UFW, including Traefik healthCheckNodePort. An Ansible-managed raw PREROUTING guard drops TCP/UDP ports 30000–32767 on public interfaces, before kube-proxy DNAT/ACCEPT. The UFW startup hook restores it, without a timer or new daemon. Inventory network interfaces and the standard NodePort range are assumed; update the guard if either changes. Use ingress 80/443 rather than public NodePorts.

Grafana requires authentication and can initiate connections only to Prometheus and cluster DNS. The static website accepts requests only from Traefik and cannot initiate network connections. Gateway/probe/exporter ingress policies remain separate. Freedom outbounds explicitly block private/reserved destination addresses at the final connection stage, including after DNS resolution, while retaining SPB's `AsIs` routing for foreign domains. This supplements routing rules; management ports on inventoried public host addresses are also blocked through the VPN, while public web/VPN ingress remains available on 80/443.

Deployment workflows check pinned SSH host keys, validate cluster names as data rather than shell source, use a read-only GitHub token and avoid persisting checkout credentials. The checkout action is pinned to a commit; Flux, kubectl and the K3s installer have fixed versions/revisions and SHA-256 checksums. Python dependencies and transitive package supply chains still require maintenance; pinning is not an assertion that software is vulnerability-free.

## Audit evidence — 3 November 2026

- Both hosts: SSH password/interactive authentication disabled; no group/world-writable files found in the checked systemd and local executable directories.
- UK: kubeconfig and datastore encryption key are root-owned mode 0600. Application default service account cannot read Secrets; anonymous Secret authorization was denied.
- Checked externally over IPv4: ports 22/80/443 reachable in UK; 22/443 reachable in SPB. The previously reachable SPB health-check port 32142 was blocked. This was a targeted TCP scan, not a complete IPv6/UDP penetration test.
- All 547 reachable Git-history blobs examined against 10 distinct active Xray credential values: no plaintext matches. Historical revoked credentials remain untrusted; unreachable GitHub objects, forks, logs and third-party caches are outside this check.
- Both merged gateway configurations validated using the deployed Xray binary before publishing. Regression tests cover RFC 7748 key derivation, private export permissions, symlink/overwrite refusal and private destination isolation.

Repeat the external checks after provisioning, Kubernetes changes and changes to service exposure. A limited audit cannot prove absence of unknown vulnerabilities or previous compromise.

References: [Kubernetes security checklist](https://kubernetes.io/docs/concepts/security/security-checklist/), [GitHub secure workflow guidance](https://docs.github.com/en/actions/reference/security/secure-use), [Xray final outbound rules](https://xtls.github.io/en/config/outbounds/freedom.html).

## Audit evidence — 4 November 2026

- Gitleaks 8.30.1 (official binary, verified checksum): current working tree has no findings. Historical findings include old credentials, an invalid example key and a checksum false positive. Historical credentials remain compromised/untrusted; deleting current files does not revoke them or remove public copies.
- All 705 reachable Git blobs were compared in memory against 15 distinct active VPN, Grafana, TLS and administrator SSH values, also checking base64-decoded strings: no matches. No secret values were logged. This cannot cover forks, deleted/unreachable objects, third-party caches or credentials outside that selected set.
- Confirmed with a real client that public control-plane port 6443 was reachable through authenticated VPN egress before the new destination-port restriction. Authentication was still required; no authorization bypass was demonstrated. Final outbound rules now deny this route on both gateways, including IPv6 and resolved hostnames.
- Targeted external IPv4 TCP checks: UK exposes 22/80/443; SPB exposes 22/443. Checked management, exporter, internal listener and representative NodePort endpoints were unreachable. Anonymous Grafana search/dashboard API reads returned 403. External IPv6 checks could not run from this workstation (no IPv6 route); firewall configuration was inspected separately. UDP and a full-port scan were not performed.
- K3s reports encryption Enabled, `reencrypt_finished`, matching server hashes. SSH password/keyboard-interactive login is disabled on both hosts. Ordinary application accounts have no Secret access.
- Removed obsolete kube-state-metrics/RBAC/rules, the unused Xray HTTP metrics listener on 11111 and old node-exporter StatsService permissions. Only Xray exporters may reach the StatsService through pod ingress policy; host-network/node traffic remains a privileged cluster trust boundary.
- Regression checks enforce ciphertext-only Git Secrets, digest-pinned non-root containers, read-only root filesystems, disabled privilege escalation and management destination restrictions. CI repeats these checks and redacted source secret scanning without infrastructure credentials.

Advisory review: [Grafana published advisories](https://grafana.com/security/security-advisories/) and [Xray GHSA-5wf9-h793-w73c](https://github.com/XTLS/Xray-core/security/advisories/GHSA-5wf9-h793-w73c) were consulted. Xray 26.10.31 is newer than that advisory's fixed 26.7.11. This targeted configuration/source audit is not a complete OS/container dependency CVE scan, a penetration test or evidence of absence of compromise.

### Remaining website build finding

`conf/www-service/runtime.yaml` still clones mutable `getrafty-org/site` and runs an npm build in root init containers with floating Git/Node/BusyBox image tags. The permission-fix init container adds CHOWN. They have no host mounts or API token, but this remains a supply-chain/resource risk. The website's egress-deny policy also means this legacy network build must not be assumed to work after a cold restart. Replacing the existing site's content or publishing a prebuilt website image is a separate content/pipeline decision; the current content was preserved during infrastructure cleanup. Do not count these init containers as covered by the runtime non-root/digest checks.
