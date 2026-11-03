"""Real TLS and SOCKS integration, including failures and metric timestamps."""
import http.server
import importlib.util
import json
from pathlib import Path
import select
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('probe_runtime', ROOT / 'services/vpn-probe/base/probe.py')
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


class Web(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        status, body = 200, b'Example website'
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/ok')
            self.end_headers()
            return
        if self.path == '/fail':
            status = 403
        if self.path == '/challenge':
            body = b'<title>Just a moment</title>Example website'
        if self.path == '/wrong':
            body = b'Other page'
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Socks(socketserver.BaseRequestHandler):
    names = []

    def handle(self):
        self.request.settimeout(2)
        self.server.seen += 1
        if probe.read_exact(self.request, 3) != b'\x05\x01\x00':
            return
        self.request.sendall(b'\x05\x00')
        if probe.read_exact(self.request, 4) != b'\x05\x01\x00\x03':
            return
        host = probe.read_exact(self.request, probe.read_exact(self.request, 1)[0]).decode()
        self.names.append(host)
        port = struct.unpack('!H', probe.read_exact(self.request, 2))[0]
        with socket.create_connection((host, port), timeout=2) as upstream:
            self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            while True:
                ready, _, _ = select.select([self.request, upstream], [], [], 2)
                if not ready:
                    return
                for source in ready:
                    try:
                        chunk = source.recv(32768)
                    except ConnectionResetError:
                        return
                    if not chunk:
                        return
                    (upstream if source is self.request else self.request).sendall(chunk)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cert, key = Path(cls.temp.name) / 'cert.pem', Path(cls.temp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        cls.web = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Web)
        cls.web.socket = tls.wrap_socket(cls.web.socket, server_side=True)
        cls.socks = Server(('127.0.0.1', 0), Socks)
        cls.socks.seen = 0
        for server in [cls.web, cls.socks]:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.trust = ssl.create_default_context(cafile=str(cert))

    @classmethod
    def tearDownClass(cls):
        for server in [cls.web, cls.socks]:
            server.shutdown()
            server.server_close()
        cls.temp.cleanup()

    def target(self, path):
        return {'site': 'example', 'url': f'https://localhost:{self.web.server_port}{path}', 'content_match': 'Example'}

    def test_direct_and_socks_use_same_tls_and_content_checks(self):
        before = self.socks.seen
        self.assertEqual(probe.fetch(self.target('/ok'), None, 2, self.trust).success, 1)
        self.assertEqual(self.socks.seen, before)
        for path in ['/ok', '/redirect']:
            result = probe.fetch(self.target(path), self.socks.server_address, 2, self.trust)
            self.assertEqual(result.success, 1, result)
        self.assertTrue(self.socks.seen > before)
        self.assertIn('localhost', Socks.names)
        for proxy in [None, self.socks.server_address]:
            for path in ['/fail', '/challenge', '/wrong']:
                result = probe.fetch(self.target(path), proxy, 2, self.trust)
                self.assertEqual(result.success, 0, result)
            self.assertEqual(probe.fetch(self.target('/ok'), proxy, 2).success, 0)

    def test_failed_proxy_never_falls_back_to_direct(self):
        with patch.object(probe, 'socks_connect', side_effect=OSError('unreachable')):
            result = probe.fetch(self.target('/ok'), ('127.0.0.1', 1), 2, self.trust)
        self.assertEqual(result.success, 0)
        self.assertEqual(result.status, 0)
        self.assertIn('unreachable', result.error)

    def test_metrics_keep_observation_time_and_unknown_cells_before_first_run(self):
        config = probe.load_config(ROOT / 'services/vpn-probe/base/targets.json')
        service = probe.Probe(config)
        initial = service.metrics().decode()
        self.assertEqual(sum(line.startswith('probe_target_info{') for line in initial.splitlines()), 11)
        self.assertNotIn('probe_success{', initial)
        service.results[('facebook', 'vpn', 'client-web')] = probe.Result(0, 403, .2, 123.456)
        metrics = service.metrics().decode()
        success = next(line for line in metrics.splitlines() if line.startswith('probe_success{'))
        self.assertTrue(success.endswith(' 0 123456'))
        self.assertIn('access="vpn"', success)
        self.assertEqual(metrics, service.metrics().decode())

    def test_total_target_timeout_and_protocol_policy(self):
        result = probe.fetch(self.target('/ok'), self.socks.server_address, -1, self.trust)
        self.assertEqual(result.success, 0)
        target = self.target('/ok');target['url'] = 'http://localhost/'
        self.assertEqual(probe.fetch(target, None, 1).success, 0)


if __name__ == '__main__':
    unittest.main()
