#!/usr/bin/env bash
# Disposable, unprivileged Alpine adapter test. No infrastructure credentials.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMP="$(mktemp -d)"
CONTAINER=""
K3S_VERSION="$(python3 -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["vm_cluster_profile"]["k3s_version"])' "$ROOT/cluster/clusters/hm-cluster/profile.yml")"
cleanup() {
  if [[ -n "$CONTAINER" ]]; then docker rm --force "$CONTAINER" >/dev/null; fi
  rm -rf "$TEMP"
}
trap cleanup EXIT
IMAGE='alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a'
CONTAINER="$(docker run --init --detach --publish 127.0.0.1::22 "$IMAGE" sleep 3600)"
# No Python: exercise the same raw bootstrap used for fresh fleet hosts.
docker exec "$CONTAINER" apk add --no-cache openrc openssh busybox-openrc >/dev/null
ssh-keygen -q -t ed25519 -N '' -f "$TEMP/key"
docker exec "$CONTAINER" mkdir -p /root/.ssh
docker exec -i "$CONTAINER" sh -c 'cat > /root/.ssh/authorized_keys; chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys' < "$TEMP/key.pub"
docker exec "$CONTAINER" sh -c 'ssh-keygen -A >/dev/null; mkdir -p /run/openrc; touch /run/openrc/softlevel; rc-service sshd start; rc-service crond start; rc-update add crond default'
PORT="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "22/tcp") 0).HostPort}}' "$CONTAINER")"
printf '[127.0.0.1]:%s ' "$PORT" > "$TEMP/known_hosts"
docker exec "$CONTAINER" cat /etc/ssh/ssh_host_ed25519_key.pub >> "$TEMP/known_hosts"
cat > "$TEMP/inventory.yml" <<EOF
all:
  hosts:
    alpine_test:
      ansible_host: 127.0.0.1
      ansible_port: $PORT
      ansible_user: root
      ansible_ssh_private_key_file: '$TEMP/key'
      ansible_ssh_common_args: '-o IdentitiesOnly=yes -o IdentityAgent=none -o UserKnownHostsFile=$TEMP/known_hosts -o StrictHostKeyChecking=yes'
      hm_machine_profile: {}
EOF
cd "$ROOT"
export ANSIBLE_CONFIG="$ROOT/vm/ansible.cfg"
ansible-playbook -i "$TEMP/inventory.yml" tests/provisioning/alpine.yml
ansible-playbook -i "$TEMP/inventory.yml" tests/provisioning/alpine.yml | tee "$TEMP/second.log"
grep -Eq '^alpine_test[[:space:]]+:.*changed=0.*failed=0' "$TEMP/second.log"
# Verify the pinned installer generates both OpenRC units; never start Kubernetes
# in a container that shares its host kernel/cgroup hierarchy.
docker exec "$CONTAINER" wget -q -O /tmp/k3s-install.sh 'https://raw.githubusercontent.com/k3s-io/k3s/01b6f04aaa69e8b09303f0393d4b4f1811da23aa/install.sh'
docker exec "$CONTAINER" sh -c 'echo "46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad  /tmp/k3s-install.sh" | sha256sum -c -'
docker exec --env INSTALL_K3S_VERSION="$K3S_VERSION" --env INSTALL_K3S_EXEC=agent --env INSTALL_K3S_SKIP_START=true "$CONTAINER" sh /tmp/k3s-install.sh
docker exec --env INSTALL_K3S_SKIP_DOWNLOAD=true --env INSTALL_K3S_EXEC=server --env INSTALL_K3S_SKIP_START=true "$CONTAINER" sh /tmp/k3s-install.sh
docker exec "$CONTAINER" sh -c '/usr/local/bin/k3s --version; sh -n /etc/init.d/k3s; sh -n /etc/init.d/k3s-agent; test -x /etc/init.d/ufw; test ! -e /etc/periodic/daily/hm-packages'
# OpenRC deliberately excludes its cgroups service inside Docker; assert that
# prerequisite in conf.d, and inspect the effective firewall dependency graph.
docker exec "$CONTAINER" sh -c 'for service in k3s k3s-agent; do deps="$(/usr/libexec/rc/bin/rc-depend -u -t ineed "$service")"; echo "$deps" | grep -qw ufw; rc_need=""; . "/etc/conf.d/$service"; echo "$rc_need" | grep -qw cgroups; done'
echo 'Alpine packages, SSH policy, disabled background jobs and K3s unit generation passed; second provisioning run made zero changes.'
