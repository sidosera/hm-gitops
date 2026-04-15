#!/usr/bin/env bash
# Usage:
#   ./vm/k3s-bootstrap.sh -e vm_hosts_file=cluster/clusters/<cluster>/hosts.yml [ansible args...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# pip install --user ansible (macOS/Linux): ensure ansible-playbook is on PATH
if command -v python3 >/dev/null 2>&1; then
  _ub="$(python3 -c "import site; print(site.getuserbase() + '/bin')" 2>/dev/null)" || true
  if [[ -n "${_ub:-}" && -d "$_ub" ]]; then
    PATH="${_ub}:$PATH"
    export PATH
  fi
fi
export ANSIBLE_CONFIG="${ANSIBLE_CONFIG:-$ROOT/vm/ansible.cfg}"
cd "$ROOT"

args=(
  -i "$ROOT/vm/inventory/localhost.yml"
  "$ROOT/vm/playbooks/bootstrap.yml"
)

exec ansible-playbook "${args[@]}" "$@"
