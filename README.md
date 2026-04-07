# Hackamonth GitOps

Single-node **[k3s](https://k3s.io/)** on a VPS: **Ansible** installs the cluster, syncs git, renders **Xray** config into **`hm-xray-config`**, applies **`k8s/`** (Kustomize), applies **getrafty** from a role template (Docker Hub image from **`controller_layout.yml`**), and templates the Xray Ingress host from **`ansible/controller_layout.yml`**. **Configuration** lives in git; **sensitive values** stay in gitignored **`local-env.yaml`**.

## Prerequisites (controller)

- **Ansible** and **Python 3** (for running the playbook from your laptop or CI).

```bash
brew install ansible
# For `make lint`: pip install -r requirements-dev.txt
```

- SSH to the target as **`secrets.deploy.ssh_user`** in **`local-env.yaml`**. Ansible uses **`become`** (sudo): either **passwordless sudo** for that user, or run **`./hm-playbook.sh --ask-become-pass`** (`-K`) and enter the sudo password when prompted.

## SSH keys (two keys total)

Keep **one SSH identity for everything else** (work, personal servers, default GitHub) and **one key only for this Hackamonth VPS + this repo’s automation**. Do not use your primary **`~/.ssh/id_ed25519`** (or whatever you use for other private infra) for HM.

| Key | Role |
|-----|------|
| **Primary** | Other hosts and daily Git — **not** for this VPS and **not** in **`GITOPS_DEPLOY_KEY`** |
| **hm-gitops** | **`ssh`**, **`./hm-playbook.sh`**, **`act`**, and **GitHub Actions** for this project — one keypair, one pubkey line on the server |

Generate the project key and install the public half on the VPS:

```bash
./scripts/new-hm-gitops-key.sh
# then add ~/.ssh/hm-gitops.pub to authorized_keys on the server
```

**SSH config** (so Ansible uses hm-gitops without `--private-key`):

```sshconfig
Host hackamonth.io
  User YOUR_UNIX_LOGIN
  IdentityFile ~/.ssh/hm-gitops
  IdentitiesOnly yes
```

Match **`Host`** / **`User`** to **`secrets.deploy.ansible_host`** and **`secrets.deploy.ssh_user`**.

## Clone

```bash
git clone https://github.com/sidosera/hm-gitops.git
cd hm-gitops
chmod +x hm-playbook.sh
```

## Secrets file: `local-env.yaml`

```bash
cp local-env.example.yaml local-env.yaml
```

| Branch | Purpose |
|--------|--------|
| **`deploy.*`** | **Inventory-only** for play 1 / **`add_host`**: **`ansible_host`**, **`ssh_user`**, **`hm_git_repo`**. |
| **`xray.*`** | e.g. **`vless_clients`** (list of UUIDs) in **`deploy/xray/config.yaml.j2`** → **`hm-xray-config`**. |

Optional: add more top-level keys under **`secrets:`** and set **`hm_ci_secret_roots`** in **`ansible/roles/hm_vps/defaults/main.yml`** if you introduce workloads that need a generic **`hm-ci`** Secret (**`envFrom`**).

## Inventory

- **`ansible/inventory/localhost.yml`** — play 1 on the controller.
- **`ansible/controller_layout.yml`** — committed layout: `hm_inventory_hostname`, **`hm_git_ref`**, **`hm_xray_public_host`**, **`hm_getrafty_image`** (Docker Hub tag for the site). SSH login is **`secrets.deploy.ssh_user`** in **`local-env.yaml`** (not committed).

Play 1 **`include_vars`** + **`add_host`** group **`hm`** with **`{{ secrets.deploy.* }}`**.

## Deploy / update / teardown

```bash
./hm-playbook.sh
./hm-playbook.sh -e hm_action=update
./hm-playbook.sh -e hm_action=teardown
./hm-playbook.sh -e hm_action=teardown -e hm_teardown_uninstall_k3s=true  # also k3s-uninstall.sh
```

Use the **hm-gitops** key (SSH config above) or **`./hm-playbook.sh --private-key ~/.ssh/hm-gitops …`**. If sudo is not passwordless:

```bash
./hm-playbook.sh --ask-become-pass
```

**Update** pulls the gitops repo on the server, re-renders Xray JSON → **`hm-xray-config`**, runs **`kubectl apply -k /srv/hm/k8s/`**, reapplies **getrafty** and the templated Xray Ingress.

## GitHub → Ansible (optional)

Workflow **[`.github/workflows/gitops-apply.yml`](.github/workflows/gitops-apply.yml)** runs **`hm-playbook.sh -e hm_action=update`** on push to **`main`**.

**Secrets (repository):**

- **`GITOPS_LOCAL_ENV_B64`** — `base64` of **`local-env.yaml`** (single line, no newlines).
- **`GITOPS_DEPLOY_KEY`** — the **same** **`~/.ssh/hm-gitops`** private key you use for **`hm-playbook`** (never your primary **`id_*`**). After **`./scripts/new-hm-gitops-key.sh`**:

```bash
gh secret set GITOPS_DEPLOY_KEY < ~/.ssh/hm-gitops
gh secret set GITOPS_LOCAL_ENV_B64 --body "$(base64 < local-env.yaml | tr -d '\n')"
```

Set **`GITOPS_DEPLOY_KEY`** with **`gh secret set`** (multiline PEM). **`./scripts/run-act-gitops.sh`** sends **`GITOPS_DEPLOY_KEY_B64`** for you from **`~/.ssh/hm-gitops`**; you do not need **`GITOPS_DEPLOY_KEY_B64`** in the repo unless you set it yourself.

**[act](https://github.com/nektos/act)** (Docker running) — reads **`~/.ssh/hm-gitops`** (same key as **`GITOPS_DEPLOY_KEY`**):

```bash
./scripts/run-act-gitops.sh
```

Use **`--dryrun`** to validate the workflow only. The container needs outbound SSH to **`secrets.deploy.ansible_host`**.

If the VPS user needs a password for **`sudo`**, set GitHub secret **`GITOPS_BECOME_PASSWORD`** or, for act only, run **`GITOPS_BECOME_PASSWORD='…' ./scripts/run-act-gitops.sh`**. Otherwise configure **`NOPASSWD`** for that user so become is non-interactive (recommended for CI).

## Server layout (after deploy)

| Path | Role |
|------|------|
| `/srv/hm` | Git checkout; **`k8s/`** applied from here |
| `/etc/hm` | `local-env.yaml`, rendered `xray/config.json` |

**k3s**: kubeconfig at **`/etc/rancher/k3s/k3s.yaml`**, workloads in namespace **`hm`**.

## Stack (Kubernetes)

- **Traefik** (bundled with k3s) + **`k8s/system/traefik-acme.yaml`** HelmChartConfig for ACME TLS.
- **getrafty** — public image from **`hm_getrafty_image`** (default **`docker.io/sidosera/getrafty-site:latest`**); change in **`ansible/controller_layout.yml`**. Manifest: **`ansible/roles/hm_vps/templates/getrafty.yaml.j2`** (Ingress hosts there too).
- **Xray** — config from Secret **`hm-xray-config`**. Optional **VLESS + REALITY** (TCP/TLS masquerade) is the strongest transport this repo wires up; WebSocket + TLS on **`/xray-ws`** remains a fallback.

**Xray** Ingress host is driven by **`hm_xray_public_host`** (Ansible template). **getrafty** hosts are in **`getrafty.yaml.j2`**.

### Xray client (import URL)

```bash
pip install PyYAML   # once, if `python3` cannot import yaml
./scripts/xray-share-url.sh
```

With **`secrets.xray.reality.enabled: true`**, this prints a **REALITY** `vless://` link (use a client with **uTLS** / REALITY support, e.g. recent v2rayNG / Nekoray). Add **`--include-ws`** to also print the WebSocket URL. With REALITY off, you get the WebSocket URL only.

**REALITY setup:** **`./scripts/xray-reality-keygen.sh`** → paste **`private_key`** / **`public_key`** into **`local-env.yaml`**, set **`dest`**, **`server_names`**, **`short_ids`**, enable **`reality.enabled`**, run **`./hm-playbook.sh -e hm_action=update`**. Confirm **`kubectl get crd ingressroutetcps.traefik.io`** exists on the cluster.

No proxy is **impossible** to block (IP/domain lists, client fingerprints, active probing, legal pressure). REALITY is a **strong practical** hardening step, not a guarantee.

## Linting

```bash
pip install -r requirements-dev.txt
brew install shellcheck
make lint
```

## More docs

- Xray: [deploy/xray/README.md](deploy/xray/README.md)
