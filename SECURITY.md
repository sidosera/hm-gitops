# Security boundaries

This repository is public. Addresses, topology, service names, routing rules and encrypted Secret metadata are public information. Server invisibility is not a security boundary. Client credentials, REALITY private keys, TLS private keys, SSH private keys and the SOPS decryption identity must remain private.

Git stores application secrets only as SOPS ciphertext. Flux decrypts them into Kubernetes Secrets; K3s encrypts Secret records in its SQLite datastore. The age recovery artifact is encrypted to the administrator's SSH key. Copying Git, knowing the public age recipient, or generating a UUID does not authorize a client: its UUID must already be allowed by the gateway. Client UUIDs are bearer credentials: anyone receiving a valid profile can use it until revoked. Export profiles only to an owner-only directory outside Git; existing files and symlinks are refused. Public-key derivation never puts a private key in a subprocess argument.

## Administrative trust

Write access to deployed Git configuration, the privileged deployment workflow, a host's root account or the Flux decryption identity is administrative access. SOPS and datastore encryption do not protect against those administrators or a running host compromise. Protect the GitHub account and SSH key accordingly. The current shared SSH deployment identity has privileged host access and can also decrypt the recovery artifact; it must be treated as a cluster administrator credential.

The unified `xray-credentials` Secret is a deliberate administrative convenience, not isolation between zones. Containers receive only their selected key through projected volumes, but a node serving a consumer can obtain the whole Secret object. Compromise of a worker's root account must therefore trigger investigation and rotation of the shared Xray credentials, not just that worker's projected profile. No claim of tenant isolation is made for this shared cluster.

## Runtime and network

Applications run without root, privilege escalation, capabilities or writable root filesystems. Application API tokens are disabled; Prometheus and kube-state-metrics have limited read permissions and no Secret permissions. Collector/probe Python runs in isolated mode (`-I`) from read-only ConfigMaps. The online collector uses a fixed absolute Xray executable, fixed endpoint and fixed output file. Its verified executable volume is read-only after initialization. There is no application systemd timer or public command-execution endpoint. A Python scheduling loop is not by itself a remote code execution boundary.

Node-exporter retains read-only host filesystem/proc/sys mounts and host networking to measure the host, but has no host PID namespace, root privileges or Kubernetes token. Those mounts remain a larger attack surface than ordinary application pods. Changing the implementation language would not remove that access.

Only SSH and required ingress/VPN ports are public. The Kubernetes API and exporter ports are restricted to cluster peers. **UFW status alone is insufficient**: kube-proxy installs ACCEPT rules ahead of UFW, including Traefik healthCheckNodePort. An Ansible-managed raw PREROUTING guard drops TCP/UDP ports 30000–32767 on public interfaces, before kube-proxy DNAT/ACCEPT. The UFW startup hook restores it, without a timer or new daemon. Inventory network interfaces and the standard NodePort range are assumed; update the guard if either changes. Use ingress 80/443 rather than public NodePorts.

Grafana requires authentication and can initiate connections only to Prometheus and cluster DNS. The static website accepts requests only from Traefik and cannot initiate network connections. Gateway/probe/exporter ingress policies remain separate. Freedom outbounds explicitly block private/reserved destination addresses at the final connection stage, including after DNS resolution, while retaining SPB's `AsIs` routing for foreign domains. This supplements routing rules; it does not expose management services through the VPN.

Deployment workflows check pinned SSH host keys, validate cluster names as data rather than shell source, use a read-only GitHub token and avoid persisting checkout credentials. The checkout action is pinned to a commit; Flux, kubectl and the K3s installer have fixed versions/revisions and SHA-256 checksums. Python dependencies and transitive package supply chains still require maintenance; pinning is not an assertion that software is vulnerability-free.

## Audit evidence — 3 November 2026

- Both hosts: SSH password/interactive authentication disabled; no group/world-writable files found in the checked systemd and local executable directories.
- UK: kubeconfig and datastore encryption key are root-owned mode 0600. Application default service account cannot read Secrets; anonymous Secret authorization was denied.
- Checked externally over IPv4: ports 22/80/443 reachable in UK; 22/443 reachable in SPB. The previously reachable SPB health-check port 32142 was blocked. This was a targeted TCP scan, not a complete IPv6/UDP penetration test.
- All 547 reachable Git-history blobs examined against 10 distinct active Xray credential values: no plaintext matches. Historical revoked credentials remain untrusted; unreachable GitHub objects, forks, logs and third-party caches are outside this check.
- Both merged gateway configurations validated using the deployed Xray binary before publishing. Regression tests cover RFC 7748 key derivation, private export permissions, symlink/overwrite refusal and malicious collector environment overrides.

Repeat the external checks after provisioning, Kubernetes changes and changes to service exposure. A limited audit cannot prove absence of unknown vulnerabilities or previous compromise.

References: [Kubernetes security checklist](https://kubernetes.io/docs/concepts/security/security-checklist/), [GitHub secure workflow guidance](https://docs.github.com/en/actions/reference/security/secure-use), [Xray final outbound rules](https://xtls.github.io/en/config/outbounds/freedom.html).
