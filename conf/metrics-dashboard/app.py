#!/usr/bin/env python3
"""Small, read-only service dashboard. No client addresses or destinations are stored."""
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request

INTERVAL = 15
RETENTION = 7 * 86400
SA = Path(os.getenv('SERVICE_ACCOUNT_DIR', '/var/run/secrets/kubernetes.io/serviceaccount'))
NAMESPACE = os.getenv('NAMESPACE', 'hm')
NODE = os.getenv('NODE_NAME', 'machine')
NODE_IP = os.getenv('NODE_IP', '87.106.74.217')
XRAY = os.getenv('XRAY_BINARY', '/tools/xray')
API_ADDRESS = os.getenv('XRAY_API', 'proxy-service.hm.svc.cluster.local:10085')
METRICS_URL = os.getenv('XRAY_METRICS', 'http://proxy-service.hm.svc.cluster.local:11111/debug/vars')
AUTH_FILE = Path(os.getenv('AUTH_FILE', '/auth/config.json'))
DATA = Path(os.getenv('DATA_DIR', '/data'))
BASE = '/status/'


def quantity(value):
    match = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)([A-Za-z]*)', str(value))
    if not match:
        raise ValueError('Invalid Kubernetes quantity')
    factors = {'': 1, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, 'k': 1e3, 'K': 1e3,
               'M': 1e6, 'G': 1e9, 'T': 1e12, 'Ki': 1024, 'Mi': 1024**2,
               'Gi': 1024**3, 'Ti': 1024**4}
    return float(match[1]) * factors[match[2]]


def get_json(url, authenticated=False):
    headers = {'Accept': 'application/json'}
    context = None
    if authenticated:
        headers['Authorization'] = 'Bearer ' + (SA / 'token').read_text().strip()
        context = ssl.create_default_context(cafile=str(SA / 'ca.crt'))
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, context=context, timeout=4) as response:
        return json.load(response)


def kube(path):
    return get_json('https://kubernetes.default.svc' + path, True)


def online_stats():
    response = subprocess.run([XRAY, 'api', 'statsonlineiplist', '--server=' + API_ADDRESS,
                               '-timeout', '3', '-all'], capture_output=True, text=True,
                              check=True, timeout=5)
    users = json.loads(response.stdout).get('users', [])
    all_ips = set()
    per_profile = {}
    for user in users:
        addresses = set()
        entries = user.get('ips', [])
        for entry in entries:
            value = entry.get('ip') if isinstance(entry, dict) else entry
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            # Only aggregate counts leave this function; never persist IP addresses.
            addresses.add(str(address))
        all_ips.update(addresses)
        per_profile[user.get('email', 'unknown')] = len(addresses)
    return {'clients': len(all_ips), 'profiles': per_profile,
            'active_profiles': sum(count > 0 for count in per_profile.values()),
            'source_preserved': os.getenv('PRIMARY_SOURCE_PRESERVED', 'false') == 'true'}


def delta_rate(value, old_value, seconds):
    if value is None or old_value is None or seconds <= 0 or value < old_value:
        return None
    return (value - old_value) / seconds


def timestamp(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / 'history.sqlite', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS samples (time REAL PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()
        self.lock = threading.Lock()
        self.latest = None
        self.previous = None
        self.previous_time = None
        self.last_cleanup = 0

    def save(self, sample, raw):
        now = sample['time']
        with self.lock:
            if self.previous is not None:
                seconds = now - self.previous_time
                for key, value in raw.items():
                    sample['rates'][key] = delta_rate(value, self.previous.get(key), seconds)
            self.previous = raw
            self.previous_time = now
            self.latest = sample
            self.db.execute('INSERT OR REPLACE INTO samples VALUES (?, ?)', (now, json.dumps(sample)))
            if now - self.last_cleanup > 3600:
                self.db.execute('DELETE FROM samples WHERE time < ?', (now - RETENTION,))
                self.last_cleanup = now
            self.db.commit()

    def snapshot(self):
        with self.lock:
            return self.latest

    def history(self, hours):
        # Sample through the timestamp index: a seven-day request must not load
        # every JSON payload into this small service's memory.
        start = time.time() - hours * 3600
        end = time.time()
        width = max(INTERVAL, (end - start) / 240)
        selected = []
        with self.lock:
            lower = start
            while lower <= end:
                row = self.db.execute(
                    'SELECT payload FROM samples WHERE time >= ? AND time < ? ORDER BY time DESC LIMIT 1',
                    (lower, min(end + 1, lower + width))).fetchone()
                if row:
                    selected.append(row)
                lower += width
        return [{'time': s['time'], 'cpu': s['host'].get('cpu_percent'),
                 'memory': s['host'].get('memory_percent'), 'clients': s['proxy'].get('clients'),
                 'upload': s['rates'].get('proxy_upload'), 'download': s['rates'].get('proxy_download')}
                for s in (json.loads(row[0]) for row in selected)]



def collect(store):
    sources = {
        'node': lambda: kube('/api/v1/nodes/' + NODE),
        'node_usage': lambda: kube('/apis/metrics.k8s.io/v1beta1/nodes/' + NODE),
        'pods': lambda: kube('/api/v1/namespaces/' + NAMESPACE + '/pods'),
        'pod_usage': lambda: kube('/apis/metrics.k8s.io/v1beta1/namespaces/' + NAMESPACE + '/pods'),
        'host_detail': lambda: get_json('https://' + ('[' + NODE_IP + ']' if ':' in NODE_IP else NODE_IP)
                                       + ':10250/stats/summary', True),
        'xray': lambda: get_json(METRICS_URL),
        'online_clients': online_stats,
    }
    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        jobs = {name: executor.submit(fn) for name, fn in sources.items()}
        values, errors = {}, []
        for name, job in jobs.items():
            try:
                values[name] = job.result()
            except Exception as error:
                errors.append(name + ': ' + type(error).__name__)
    now = time.time()
    result = {'time': now, 'interval': INTERVAL, 'host': {}, 'proxy': {}, 'pods': [],
              'rates': {}, 'errors': errors}
    raw = {}
    capacity = values.get('node', {}).get('status', {}).get('capacity', {})
    host = result['host']
    host['name'] = NODE
    host['cores'] = quantity(capacity.get('cpu', 0))
    host['memory_total'] = quantity(capacity.get('memory', 0))
    usage = values.get('node_usage', {}).get('usage', {})
    if 'cpu' in usage and host['cores']:
        host['cpu_percent'] = quantity(usage['cpu']) / host['cores'] * 100
    if 'memory' in usage and host['memory_total']:
        host['memory_used'] = quantity(usage['memory'])
        host['memory_percent'] = host['memory_used'] / host['memory_total'] * 100
    summary = values.get('host_detail', {}).get('node', {})
    if summary.get('startTime'):
        host['uptime'] = max(0, now - timestamp(summary['startTime']))
    fs = summary.get('fs', {})
    if fs.get('capacityBytes'):
        host['disk_total'] = fs['capacityBytes']
        host['disk_used'] = fs.get('usedBytes', 0)
        host['disk_percent'] = host['disk_used'] / host['disk_total'] * 100
    memory = summary.get('memory', {})
    if 'availableBytes' in memory:
        host['memory_available'] = memory['availableBytes']
    interfaces = summary.get('network', {}).get('interfaces', [])
    physical = [interface for interface in interfaces if interface.get('name', '').startswith(('en', 'eth'))]
    if physical:
        raw['host_receive'] = sum(interface.get('rxBytes', 0) for interface in physical)
        raw['host_send'] = sum(interface.get('txBytes', 0) for interface in physical)
    online = values.get('online_clients')
    if online is not None:
        result['proxy'].update(online)
    xray = values.get('xray')
    if xray is not None:
        inbounds = xray.get('stats', {}).get('inbound', {})
        primary = {direction: sum(int(inbounds.get(name, {}).get(direction, 0))
                                   for name in ('reality', 'reality-legacy'))
                   for direction in ('uplink', 'downlink')}
        result['proxy']['transports'] = {'reality': primary,
                                        'https-backup': inbounds.get('https-backup', {'uplink': 0, 'downlink': 0})}
        raw['proxy_upload'] = sum(int(value.get('uplink', 0)) for name, value in inbounds.items()
                                  if name in ('reality', 'reality-legacy', 'https-backup'))
        raw['proxy_download'] = sum(int(value.get('downlink', 0)) for name, value in inbounds.items()
                                    if name in ('reality', 'reality-legacy', 'https-backup'))
        result['proxy']['upload_total'] = raw['proxy_upload']
        result['proxy']['download_total'] = raw['proxy_download']
    pod_usage = {pod['metadata']['name']: pod for pod in values.get('pod_usage', {}).get('items', [])}
    for pod in values.get('pods', {}).get('items', []):
        name = pod['metadata']['name']
        statuses = pod.get('status', {}).get('containerStatuses', [])
        containers = pod_usage.get(name, {}).get('containers', [])
        status = pod.get('status', {}).get('phase', 'Unknown')
        if status == 'Succeeded' or pod['metadata'].get('deletionTimestamp'):
            continue
        item = {'name': name, 'service': pod['metadata'].get('labels', {}).get('app.kubernetes.io/name', name),
                'ready': bool(statuses) and all(c.get('ready', False) for c in statuses), 'status': status,
                'restarts': sum(c.get('restartCount', 0) for c in statuses),
                'memory': sum(quantity(c['usage']['memory']) for c in containers) if containers else None,
                'cpu': sum(quantity(c['usage']['cpu']) for c in containers) if containers else None}
        result['pods'].append(item)
    store.save(result, raw)


def collector(store):
    while True:
        started = time.monotonic()
        try:
            collect(store)
        except Exception as error:
            print('Collection failed: ' + type(error).__name__, flush=True)
        time.sleep(max(1, INTERVAL - (time.monotonic() - started)))


class Handler(BaseHTTPRequestHandler):
    store = None

    def log_message(self, *args):
        pass

    def send(self, status, body, content_type='application/json', extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization', '')
        if len(header) > 4096 or not header.startswith('Basic '):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True)
            expected = json.loads(AUTH_FILE.read_text())['credential_sha256']
            return hmac.compare_digest(hashlib.sha256(decoded).hexdigest(), expected)
        except (ValueError, OSError, KeyError, json.JSONDecodeError):
            return False

    def do_GET(self):
        uri = urllib.parse.urlsplit(self.path)
        if uri.path == '/healthz':
            sample = self.store.snapshot()
            healthy = sample is not None and time.time() - sample['time'] < INTERVAL * 4
            return self.send(200 if healthy else 503, {'ready': healthy})
        if not self.authorized():
            return self.send(401, {'error': 'Authentication required'}, extra={'WWW-Authenticate': 'Basic realm="VPS metrics", charset="UTF-8"'})
        if uri.path == '/status':
            return self.send(302, b'', extra={'Location': BASE})
        if uri.path == BASE:
            return self.send(200, Path(__file__).with_name('index.html').read_bytes(), 'text/html; charset=utf-8')
        if uri.path == BASE + 'api/current':
            sample = self.store.snapshot()
            return self.send(200 if sample else 503, sample or {'error': 'Waiting for first sample'})
        if uri.path == BASE + 'api/history':
            try:
                hours = int(urllib.parse.parse_qs(uri.query).get('hours', ['1'])[0])
            except ValueError:
                return self.send(400, {'error': 'Invalid history window'})
            return self.send(200, self.store.history(max(1, min(168, hours))))
        return self.send(404, {'error': 'Not found'})


def main():
    # Fail closed if the mounted authentication config is absent or malformed.
    digest = json.loads(AUTH_FILE.read_text())['credential_sha256']
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid authentication configuration')
    Handler.store = Store(DATA)
    threading.Thread(target=collector, args=(Handler.store,), daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '8080'))), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    main()
