# Common operations

## Setup

Download the `hmctl` artifact from GitHub Actions and verify `SHA256SUMS`, or build locally with Go 1.27.3+:

```sh
make build
export PATH="$PWD/bin:$PATH"
export SOPS_AGE_KEY_FILE=/secure/path/flux-age.agekey
hmctl --help
```

Run from the repository, or pass `--repo /path/to/hm-gitops`. The age identity must be owned by you with mode `0600`. PR creation requires Git and authenticated `gh`; only profile checks require Xray in `PATH`.

## Change workflow

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

## Add, list or revoke an account

```sh
hmctl service gateway --add-account --label alice
hmctl service gateway --list-accounts --json
hmctl service gateway --remove-account --label alice
```

Labels may be short names or email addresses. `probe-e2e@service-account` is an ordinary label convention. Adding an existing label reuses its UUID; revocation defaults to all zones.

## Issue or update a client profile

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

## Check connectivity or configure the probe

```sh
hmctl service gateway --check-profile /secure/path/alice/hm-reality-ipv4-chrome.txt

hmctl service vpn-probe --sync-credentials \
  --client-id-file /secure/path/probe-client.uuid
```

Profile checks verify exit IP, HTTPS and a transfer from the machine running the command. Use `--expected-exit-ip NEW_EXIT_IP` when the expected exit differs from the control-plane inventory address.

The probe UUID file must contain one existing UUID authorized on each gateway referenced by the probes, and have mode `0600`. Create access through the normal gateway account workflow first. Probe sync updates encrypted runtime configuration through a PR; it never creates accounts or changes labels.

## Replace a machine

Verify its SSH host key independently through the provider console, then prepare the inventory PR:

```sh
hmctl cluster host replace ru-spb-1/1 --ip NEW_PUBLIC_IPV4 \
  --ssh-host-key 'ssh-ed25519 VERIFIED_PUBLIC_HOST_KEY'
```

Logical host identity, private addresses and service credentials stay unchanged. Supply `--ipv6 NEW_PUBLIC_IPV6` to retain IPv6; otherwise the previous public IPv6 is removed.

After merging, provision/converge hosts from `main`:

```sh
gh workflow run cluster-converge.yml --ref main -f cluster_name=hm-cluster
```

Verify service health before moving DNS or distributing updated profiles. Replacing the sole control plane also requires datastore recovery or an explicit cluster rebuild/rejoin; an inventory PR alone is insufficient.

## Inspect deployment health

Run on the control-plane host over SSH:

```sh
sudo kubectl -n flux-system get gitrepositories,kustomizations
sudo kubectl -n hm get pods,svc
sudo kubectl -n hm logs deployment/proxy-service --tail=20
```

Keep private identities and exported profiles outside Git. Do not restore revoked credentials when rolling back an application change.
