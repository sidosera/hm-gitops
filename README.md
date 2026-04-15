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

For app config files, secret-bearing keys use the `_secret` suffix and are base64-encoded before encryption. The proxy init container decodes those fields and converts the YAML source to the final JSON config.

## Current Cluster

Referenced node IPs:

- [hosts.yml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/edge-01/hosts.yml#L1)

Cluster-wide Kubernetes:

- [cluster/clusters/edge-01/kustomization.yaml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/edge-01/kustomization.yaml#L1)

Cluster rollout:

- [cluster/clusters/edge-01/kustomization.yaml](/Users/sidosera/dist/hackamonth-gitops/cluster/clusters/edge-01/kustomization.yaml#L1)

Current public endpoints:

- `https://www.hackamonth.io`
- `hackamonth.io:443`
