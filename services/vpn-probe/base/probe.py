"""Paired HTTPS connectivity checks. No public trigger API or third-party dependencies."""
import concurrent.futures
import dataclasses
import http.client
import json
import logging
import re
import signal
import socket
import ssl
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit

LOG = logging.getLogger('vpn-probe')
CHALLENGE = re.compile(rb'(?is)<title>\s*(Just a moment|Access Denied|Attention Required)')
TLS = ssl.create_default_context()


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise OSError('SOCKS connection closed')
        data.extend(part)
    return bytes(data)


def socks_connect(proxy, host, port, timeout):
    """SOCKS5 domain address keeps destination resolution inside Xray/VPN."""
    sock = socket.create_connection(proxy, timeout=timeout)
    try:
        sock.sendall(b'\x05\x01\x00')
        if read_exact(sock, 2) != b'\x05\x00':
            raise OSError('SOCKS authentication negotiation failed')
        name = host.encode('idna')
        if len(name) > 255:
            raise ValueError('SOCKS hostname too long')
        sock.sendall(b'\x05\x01\x00\x03' + bytes([len(name)]) + name + struct.pack('!H', port))
        version, status, reserved, kind = read_exact(sock, 4)
        if (version, status, reserved) != (5, 0, 0):
            raise OSError('SOCKS connect failed')
        size = {1: 4, 4: 16}.get(kind)
        if kind == 3:
            size = read_exact(sock, 1)[0]
        if size is None:
            raise OSError('Invalid SOCKS address type')
        read_exact(sock, size + 2)
        return sock
    except BaseException:
        sock.close()
        raise


class HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, proxy, timeout, context=TLS):
        super().__init__(host, port, timeout=timeout, context=context)
        self.proxy = proxy

    def connect(self):
        if self.proxy is None:
            return super().connect()
        sock = socks_connect(self.proxy, self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


@dataclasses.dataclass(frozen=True)
class Result:
    success: int
    status: int
    duration: float
    completed: float
    error: str = ''


def fetch(target, proxy, timeout, context=TLS):
    started = time.monotonic()
    deadline = started + timeout
    status = 0
    try:
        url = target['url']
        for redirect in range(6):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Only HTTPS URLs without credentials are allowed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Probe deadline exceeded')
            conn = HTTPSConnection(parsed.hostname, parsed.port or 443, proxy, remaining, context)
            response = None
            try:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                conn.request('GET', path, headers={'User-Agent': 'hm-vpn-probe/1.0', 'Accept-Encoding': 'identity', 'Connection': 'close'})
                response = conn.getresponse()
                status = response.status
                if status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location:
                        raise ValueError('Redirect without Location')
                    url = urljoin(url, location)
                    continue
                if status != 200:
                    raise ValueError('Expected HTTP 200')
                # Bound memory and total transfer. Markers span chunks via overlap.
                marker = re.compile(target['content_match'].encode())
                matched = False
                seen = 0
                tail = b''
                while not response.isclosed():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Probe deadline exceeded')
                    # HTTPResponse can detach the socket when Connection: close is sent.
                    response.fp.raw._sock.settimeout(remaining)
                    chunk = response.read1(min(32768, 2 * 1024 * 1024 - seen))
                    if not chunk:
                        break
                    seen += len(chunk)
                    window = tail + chunk
                    if CHALLENGE.search(window):
                        raise ValueError('Challenge page')
                    matched = matched or bool(marker.search(window))
                    tail = window[-4096:]
                    if seen >= 2 * 1024 * 1024:
                        break
                if not matched:
                    raise ValueError('Expected page marker absent')
                return Result(1, status, time.monotonic() - started, time.time())
            finally:
                if response is not None:
                    response.close()
                conn.close()
        raise ValueError('Too many redirects')
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return Result(0, status, time.monotonic() - started, time.time(), type(exc).__name__ + ': ' + str(exc))


def load_config(path):
    with open(path) as source:
        config = json.load(source)
    interval = config.get('interval_seconds', 60)
    timeout = config.get('timeout_seconds', 20)
    if not 0 < timeout < interval:
        raise ValueError('Timeout must be smaller than interval')
    sites = config['websites']
    names = set()
    for target in sites:
        if target['site'] in names:
            raise ValueError('Duplicate site')
        names.add(target['site'])
        re.compile(target['content_match'].encode())
        parsed = urlsplit(target['url'])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid target URL')
    if not 1 <= len(sites) <= 20:
        raise ValueError('Expected 1..20 targets')
    return config


def label_string(labels):
    return ','.join(key + '=' + json.dumps(str(value), ensure_ascii=True) for key, value in labels.items())


class Probe:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.results = {}
        self.stop = threading.Event()

    def checks(self):
        for target in self.config['websites']:
            for access in ('direct', 'vpn'):
                yield target, access, 'client-web'
        yield self.config['exit'], 'vpn', 'client-exit'

    def run_check(self, target, access, check):
        proxy = tuple(self.config['socks_proxy']) if access == 'vpn' else None
        result = fetch(target, proxy, self.config.get('timeout_seconds', 20))
        with self.lock:
            self.results[(target['site'], access, check)] = result
        if not result.success:
            LOG.warning('site=%s access=%s error=%s', target['site'], access, result.error)

    def run(self):
        checks = list(self.checks())
        # Bounded independent checks; a blocked direct route cannot delay VPN results.
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as pool:
            while not self.stop.is_set():
                start = time.monotonic()
                futures = [pool.submit(self.run_check, *check) for check in checks]
                for future in futures:
                    try:
                        future.result()
                    except Exception:
                        LOG.exception('Unexpected probe failure')
                self.stop.wait(max(0, self.config.get('interval_seconds', 60) - (time.monotonic() - start)))

    def metrics(self):
        with self.lock:
            results = dict(self.results)
        lines = ['# HELP probe_target_info Configured connectivity checks.', '# TYPE probe_target_info gauge']
        for target, access, check in self.checks():
            labels = {'site': target['site'], 'access': access, 'check': check,
                      'probe_scope': 'public-client' if access == 'vpn' else 'direct-baseline'}
            if access == 'vpn':
                labels.update(target_zone=self.config['vpn_entry_zone'], target_service='gateway')
            lines.append(f'probe_target_info{{{label_string(labels)}}} 1')
        for metric, help_text in [('probe_success', 'HTTPS status and content checks passed.'),
                                  ('probe_http_status_code', 'Final observed HTTP status, zero before a response.'),
                                  ('probe_duration_seconds', 'Time spent checking the target.'),
                                  ('probe_last_run_timestamp_seconds', 'Unix time of the last completed check.')]:
            lines.extend([f'# HELP {metric} {help_text}', f'# TYPE {metric} gauge'])
            for (site, access, check), result in results.items():
                labels = {'site': site, 'access': access, 'check': check,
                          'probe_scope': 'public-client' if access == 'vpn' else 'direct-baseline'}
                if access == 'vpn':
                    labels['target_zone'] = self.config['vpn_entry_zone']
                    labels['target_service'] = 'gateway'
                value = {'probe_success': result.success, 'probe_http_status_code': result.status,
                         'probe_duration_seconds': result.duration,
                         'probe_last_run_timestamp_seconds': result.completed}[metric]
                # Original completion timestamps make [1m] mean actual observations,
                # rather than repeated scrapes of an old cached result.
                lines.append(f'{metric}{{{label_string(labels)}}} {value} {int(result.completed * 1000)}')
        return ('\n'.join(lines) + '\n').encode()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/probe/targets.json')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    probe = Probe(load_config(args.config))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                body = probe.metrics()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            elif self.path == '/-/healthy':
                body = b'ok\n'
                self.send_response(200)
            else:
                self.send_error(404)
                return
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    threading.Thread(target=probe.run, daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', 9115), Handler)
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: (probe.stop.set(), threading.Thread(target=server.shutdown).start()))
    try:
        server.serve_forever()
    finally:
        probe.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
