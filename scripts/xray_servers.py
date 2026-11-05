"""Prepare physical host replacement in inventory; never provision or rotate client keys."""
import copy
import ipaddress
from cryptography.hazmat.primitives.serialization import load_ssh_public_key


def replace_server(inventory, known_hosts, slot, address, host_key, ipv6=None):
    address = str(ipaddress.IPv4Address(address))
    if not ipaddress.ip_address(address).is_global:
        raise ValueError('New server must have a public IPv4 address')
    matches = [(host, machine) for host, machine in inventory['vm_node_settings'].items() if machine['host'] == slot]
    if len(matches) != 1:
        raise ValueError('Select exactly one existing host slot')
    old, machine = matches[0]
    if old == address or address in inventory['vm_node_settings']:
        raise ValueError('New address must differ from all current machine addresses')
    # A syntactically valid key is not proof of identity: the operator must verify it out of band.
    parts = host_key.split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise ValueError('Provide the new host ed25519 public key verified through the provider console')
    key = ' '.join(parts[:2])
    load_ssh_public_key(key.encode())
    for line in known_hosts.splitlines():
        fields = line.split()
        if len(fields) >= 3 and not line.startswith('#') and address in fields[0].split(','):
            if ' '.join(fields[1:3]) != key:
                raise ValueError('New address already has a different pinned SSH key; review trust explicitly')
    result = copy.deepcopy(inventory)
    updated = copy.deepcopy(machine)
    updated['name'] = address
    updated.pop('ipv6', None)
    if ipv6:
        parsed = ipaddress.IPv6Address(ipv6)
        if not parsed.is_global:
            raise ValueError('Provide a public IPv6 address or omit --ipv6')
        updated['ipv6'] = str(parsed)
    result['vm_node_settings'] = {address if host == old else host: updated if host == old else value
                                  for host, value in result['vm_node_settings'].items()}
    for field in ['vm_hosts','vm_agent_hosts']:
        result[field] = [address if host == old else host for host in result.get(field, [])]
    lines = []
    for line in known_hosts.splitlines():
        if line.strip() and not line.startswith('#') and old in line.split()[0].split(','):
            remaining = [h for h in line.split()[0].split(',') if h != old]
            if remaining:
                lines.append(','.join(remaining) + ' ' + ' '.join(line.split()[1:]))
        else:
            lines.append(line)
    lines.append(address + ' ' + key)
    return result, '\n'.join(lines) + '\n'
