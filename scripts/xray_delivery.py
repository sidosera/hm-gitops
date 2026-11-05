"""Prepare a private connection email only after its account PR is deployed."""
import base64
from email.message import EmailMessage
import ipaddress
import json
import os
import re
from string import Template
import subprocess

import yaml
from xray_share_url import derive_public_key, export_profiles, write_private_profile


def verify_delivery_state(pr, flux, head):
    if pr.get('state') != 'MERGED' or pr.get('baseRefName') != 'main' or not pr.get('mergeCommit'):
        raise ValueError('Account PR must be merged into main before preparing instructions')
    if not any(f['path'] == 'conf/xray/credentials-secret.sops.yaml' for f in pr.get('files', [])):
        raise ValueError('PR does not change client credentials')
    status = flux.get('status', {})
    if status.get('observedGeneration') != flux['metadata']['generation']:
        raise ValueError('Flux has not observed the current configuration')
    if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in status.get('conditions', [])):
        raise ValueError('Flux is not Ready; do not deliver instructions yet')
    if status.get('lastAppliedRevision', '').split(':')[-1] != head:
        raise ValueError('Local checkout and deployed revision differ; update and retry')


def prepare_message(repo, args):
    def run(command):
        return subprocess.run(command, cwd=repo, capture_output=True, check=True).stdout
    if run(['git', 'status', '--porcelain']):
        raise ValueError('Use a clean checkout of the deployed main revision')
    head = run(['git', 'rev-parse', 'HEAD']).decode().strip()
    pr = json.loads(run(['gh', 'pr', 'view', str(args.pr), '--json', 'state,baseRefName,mergeCommit,files']))
    # Refuse unmerged requests before contacting the infrastructure or decrypting anything.
    if pr.get('state') != 'MERGED':
        raise ValueError('Account PR has not been merged')
    subprocess.run(['git', 'merge-base', '--is-ancestor', pr['mergeCommit']['oid'], head],
                   cwd=repo, capture_output=True, check=True)
    inventory = yaml.safe_load((repo / 'cluster/clusters/hm-cluster/hosts.yml').read_text())
    address = str(ipaddress.ip_address(inventory['vm_hosts'][0]))
    defaults = yaml.safe_load((repo / 'vm/group_vars/all.yml').read_text())
    user = inventory['vm_node_settings'][address].get('user', defaults['ansible_user'])
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        raise ValueError('Invalid inventory SSH user')
    flux = json.loads(run(['ssh', '-i', str(args.ssh_key.expanduser()), '-o', 'IdentitiesOnly=yes',
                          '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                          '-o', 'UserKnownHostsFile=' + str(repo / 'vm/ssh_known_hosts'), user + '@' + address,
                          'sudo -n k3s kubectl get kustomization hm-cluster -n flux-system -o json']))
    verify_delivery_state(pr, flux, head)
    secret = yaml.safe_load(run(['sops', '--decrypt', str(repo / 'conf/xray/credentials-secret.sops.yaml')]))
    profiles = {}
    configs = {k.removesuffix('.gateway.json'): json.loads(base64.b64decode(v))
               for k, v in secret['data'].items() if k.endswith('.gateway.json')}
    zones = args.zone or sorted(configs)
    for zone in zones:
        if zone not in configs:
            raise ValueError('Unknown gateway zone')
        config = configs[zone]
        tag = 'reality-legacy' if any(i.get('tag') == 'reality-legacy' for i in config['inbounds']) else 'gateway'
        config['inbounds'] = [i for i in config['inbounds'] if i.get('tag') == tag or
                             i.get('streamSettings', {}).get('network') == 'xhttp']
        reality = next(i['streamSettings']['realitySettings'] for i in config['inbounds']
                       if i.get('streamSettings', {}).get('security') == 'reality')
        hosts = [host for host, machine in inventory['vm_node_settings'].items()
                 if machine.get('zone') == zone and machine.get('gateway_entry')]
        if not hosts:
            raise ValueError('No public gateway addresses configured for selected zone')
        exported = export_profiles(config, hosts, derive_public_key(reality['privateKey']),
                                   'www.hackamonth.io', args.email)
        profiles.update({zone + ' / ' + name: url for name, url in exported.items()})
    if not profiles or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', args.email):
        raise ValueError('No public client profiles match the recipient email')
    body = Template(args.template.read_text()).substitute(
        email=args.email, profiles='\n\n'.join(name + '\n' + url for name, url in profiles.items()))
    message = EmailMessage()
    message['To'] = args.email
    message['Subject'] = args.subject
    message.set_content(body)
    output = args.output.expanduser().absolute()
    parent = output.parent.resolve()
    if parent == repo or repo in parent.parents:
        raise ValueError('Connection emails must be written outside the repository')
    info = parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Output directory must be owned by you and private (chmod 700)')
    write_private_profile(parent / output.name, message.as_string())
    print('Private email draft prepared. No email sent; no credentials printed.')
