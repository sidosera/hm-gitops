#!/usr/bin/env python3
"""Validate physical inventory and render bootstrap-owned public VPN DNS records."""
import argparse
import ipaddress
from pathlib import Path
import re
import yaml


def validate(inventory, profile):
    machines = inventory['vm_node_settings']
    hosts = inventory['vm_hosts'] + inventory.get('vm_agent_hosts', [])
    if len(set(hosts)) != len(hosts) or set(hosts) != set(machines):
        raise ValueError('Host lists and machine inventory must agree without duplicates')
    network = profile['vm_cluster_profile']['network']
    subnet = ipaddress.IPv4Network(network['cidr'])
    if not subnet.is_private or network['interface'] != 'hm0' or network['port'] != 51822:
        raise ValueError('Invalid private transport profile')
    private = set()
    private6 = set()
    subnet6 = ipaddress.IPv6Network(network['cidr_ipv6'])
    if not subnet6.is_private:
        raise ValueError('IPv6 transport must be private')
    slots = set()
    for address, machine in machines.items():
        ipaddress.IPv4Address(address)
        node_ip = ipaddress.IPv4Address(machine['private_ipv4'])
        if node_ip not in subnet or node_ip in (subnet.network_address, subnet.broadcast_address) or node_ip in private:
            raise ValueError('Private addresses must be unique usable addresses in the cluster subnet')
        private.add(node_ip)
        node_ip6 = ipaddress.IPv6Address(machine['private_ipv6'])
        if node_ip6 not in subnet6 or node_ip6 == subnet6.network_address or node_ip6 in private6:
            raise ValueError('Private IPv6 addresses must be distinct usable addresses in the cluster subnet')
        private6.add(node_ip6)
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', machine['zone']):
            raise ValueError('Zone must be a DNS label')
        if machine['host'] in slots:
            raise ValueError('Each machine must occupy a distinct logical host slot')
        slots.add(machine['host'])


def public_discovery(inventory):
    """Only these inventory-owned EndpointSlices contain physical entry addresses."""
    by_zone = {}
    for address, machine in inventory['vm_node_settings'].items():
        if machine.get('gateway_entry'):
            by_zone.setdefault(machine['zone'], []).append(str(ipaddress.IPv4Address(address)))
    result = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {
        'name': 'hm', 'labels': {'hm.gitops/discovery': 'public-entry'}}}]
    for zone, addresses in sorted(by_zone.items()):
        name = f'vpn-entry-{zone}'
        labels = {'hm.gitops/discovery': 'public-entry'}
        result += [
            {'apiVersion': 'v1', 'kind': 'Service',
             'metadata': {'name': name, 'namespace': 'hm', 'labels': labels},
             'spec': {'clusterIP': 'None', 'ports': [{'name': 'vpn', 'port': 443, 'protocol': 'TCP'}]}},
            {'apiVersion': 'discovery.k8s.io/v1', 'kind': 'EndpointSlice',
             'metadata': {'name': name, 'namespace': 'hm', 'labels': {
                 **labels, 'kubernetes.io/service-name': name,
                 'endpointslice.kubernetes.io/managed-by': 'hm-bootstrap'}},
             'addressType': 'IPv4', 'ports': [{'name': 'vpn', 'port': 443, 'protocol': 'TCP'}],
             'endpoints': [{'addresses': [address], 'conditions': {'ready': True}} for address in sorted(addresses)]}]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    inventory = yaml.safe_load(args.inventory.read_text())
    profile = yaml.safe_load(args.inventory.with_name('profile.yml').read_text())
    validate(inventory, profile)
    if not args.validate_only:
        print(yaml.safe_dump_all(public_discovery(inventory), sort_keys=False), end='')


if __name__ == '__main__':
    main()
