# Hackamonth GitOps

Single-node **[k3s](https://k3s.io/)** on a VPS: **Ansible** installs the cluster, syncs git, renders **Xray** config into **`hm-xray-config`**, applies **`k8s/`** (Kustomize), applies **getrafty** from a role template (Docker Hub image from **`controller_layout.yml`**), and templates the Xray Ingress host from **`ansible/controller_layout.yml`**. **Configuration** lives in git; **sensitive values** stay in gitignored **`local-env.yaml`**.

## Prerequisites (controller)

- **Ansible** and **Python 3** (for running the playbook from your laptop or CI).

```bash
brew install ansible
# For `make lint`: pip install -r requirements-dev.txt
```

- SSH to the target as **`secrets.deploy.ssh_user`** in **`local-env.yaml`**. Ansible uses **`become`** (sudo): either **passwordless sudo** for that user, or run **`./hm-playbook.sh --ask-become-pass`** (`-K`) and enter the sudo password when prompted.

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

Add `--private-key ~/.ssh/id_ed25519` if needed. If sudo is not passwordless:

```bash
./hm-playbook.sh --ask-become-pass
```

**Update** pulls the gitops repo on the server, re-renders Xray JSON → **`hm-xray-config`**, runs **`kubectl apply -k /srv/hm/k8s/`**, reapplies **getrafty** and the templated Xray Ingress.

## GitHub → Ansible (optional)

Workflow **[`.github/workflows/gitops-apply.yml`](.github/workflows/gitops-apply.yml)** runs **`hm-playbook.sh -e hm_action=update`** on push to **`main`**. Configure secrets **`GITOPS_LOCAL_ENV_B64`** (base64 of `local-env.yaml`) and **`GITOPS_SSH_KEY`** (paste the full private key in the Actions UI). Optionally set **`GITOPS_SSH_KEY_B64`** (base64 of the key, single line) if you prefer storing one line; the workflow accepts either **`GITOPS_SSH_KEY_B64`** or **`GITOPS_SSH_KEY`**.

To emulate that job locally with **[act](https://github.com/nektos/act)** (Docker must be running), keep **`local-env.yaml`** at the repo root and run:

```bash
./scripts/run-act-gitops.sh
```

That passes **`GITOPS_LOCAL_ENV_B64`** and **`GITOPS_SSH_KEY_B64`** derived from **`local-env.yaml`** and **`~/.ssh/id_ed25519`** (override with **`GITOPS_SSH_KEY_FILE`**). Use **`./scripts/run-act-gitops.sh --dryrun`** to print the plan without executing. The container needs outbound SSH to your **`secrets.deploy.ansible_host`**.

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
- **Xray** — config from Secret **`hm-xray-config`**.

**Xray** Ingress host is driven by **`hm_xray_public_host`** (Ansible template). **getrafty** hosts are in **`getrafty.yaml.j2`**.

## Linting

```bash
pip install -r requirements-dev.txt
brew install shellcheck
make lint
```

## More docs

- Xray: [deploy/xray/README.md](deploy/xray/README.md)
