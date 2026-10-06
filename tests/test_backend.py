import contextlib
import http.server
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend


class FakeClient:
    key = 'management-secret-example'
    url = 'https://example.test/v0/management'

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, endpoint, optional=False):
        self.calls.append(endpoint)
        return self.responses.get(endpoint)


@contextlib.contextmanager
def server(responses):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get('Authorization')))
            code, body, extra = responses.get(self.path, (404, {}, {}))
            self.send_response(code)
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length))
            calls.append((self.path, self.headers.get('Authorization'), payload))
            code, body, extra = responses.get(self.path, (404, {}, {}))
            self.send_response(code)
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    service = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(service.server_port), calls
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


class URLTests(unittest.TestCase):
    def test_normalization(self):
        for input_url, expected in [
            ('https://example.test', 'https://example.test/v0/management'),
            ('https://example.test/proxy/management.html', 'https://example.test/proxy/v0/management'),
            ('https://example.test/proxy/v0/management/', 'https://example.test/proxy/v0/management'),
            ('https://example.test/v8/management', 'https://example.test/v0/management'),
            ('http://[::1]:8317', 'http://[::1]:8317/v0/management'),
            ('http://localhost:8317', 'http://localhost:8317/v0/management')]:
            with self.subTest(input_url=input_url):
                self.assertEqual(backend.normalize_url(input_url), expected)

    def test_reject_unsafe_destinations(self):
        for url in ['http://example.test', 'http://192.168.1.2', 'http://127.0.0.1.evil.test',
                    'http://2130706433', 'ftp://example.test', 'https://user:key@example.test',
                    'https://example.test?secret=a', 'https://example.test#key', 'https://example.test?',
                    'https://example.test:bad', 'https://example.test:0', 'https://example.test/a/../b',
                    'https://example.test/%2e%2e', 'https://example.test/\nsecret',
                    'https://example.test\\@evil.test']:
            with self.subTest(url=url), self.assertRaises(backend.SafeError):
                backend.normalize_url(url)

    def test_key_header_injection(self):
        for key in ['bad\r\nX-Secret: test', '', 'with space', 'é']:
            with self.assertRaises(backend.SafeError):
                backend.validate_key(key)


class HTTPTests(unittest.TestCase):
    def test_auth_header_and_optional_missing(self):
        with server({'/v0/management/auth-files': (200, {'files': []}, {})}) as (url, calls):
            client = backend.Client(url, 'test-management')
            self.assertEqual(client.get('/auth-files'), {'files': []})
            self.assertIsNone(client.get('/usage', optional=True))
            self.assertEqual(calls[0], ('/v0/management/auth-files', 'Bearer test-management'))

    def test_auth_rejection_is_safe_and_stops_probe(self):
        for status in [401, 403]:
            with server({'/v0/management/auth-files': (status, {'error': 'UPSTREAM-SECRET'}, {})}) as (url, calls):
                with tempfile.TemporaryDirectory() as root:
                    bridge = backend.Bridge(backend.CredentialStore(root))
                    result = bridge.command({'op': 'connect', 'url': url, 'key': 'test-management'})
                    self.assertEqual(result['type'], 'error')
                    self.assertFalse(result['retryable'])
                    self.assertFalse(result['configured'])
                    self.assertNotIn('SECRET', json.dumps(result))
                    self.assertEqual(len(calls), 1)

    def test_redirect_is_never_followed(self):
        responses = {'/v0/management/auth-files': (302, {}, {'Location': '/stolen'})}
        with server(responses) as (url, calls):
            with self.assertRaises(backend.SafeError) as error:
                backend.Client(url, 'test-management').get('/auth-files')
            self.assertIn('redirected', str(error.exception))
            self.assertEqual(len(calls), 1)

    def test_bounds_and_malformed_response(self):
        for body, headers in [(b'x' * 129, {}), ({}, {'Content-Length': '1000000'}), (b'not-json', {}), ([], {})]:
            with server({'/v0/management/auth-files': (200, body, headers)}) as (url, _):
                with patch.object(backend, 'MAX_RESPONSE', 128), self.assertRaises(backend.SafeError):
                    backend.Client(url, 'test-management').get('/auth-files')

    def test_https_context_verifies_and_ignores_proxies(self):
        with patch.dict(os.environ, {'HTTPS_PROXY': 'https://must-not-use.invalid'}):
            with patch.object(backend.http.client, 'HTTPSConnection') as connection:
                connection.return_value.request.side_effect = ssl.SSLError('SECRET')
                with self.assertRaises(backend.SafeError) as error:
                    backend.Client('https://example.test', 'test-management').get('/auth-files')
                args, kwargs = connection.call_args
                self.assertEqual(args[0], 'example.test')
                self.assertTrue(kwargs['context'].check_hostname)
                self.assertEqual(kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
                self.assertNotIn('SECRET', str(error.exception))


class AggregationTests(unittest.TestCase):
    def test_legacy_usage_and_redaction(self):
        secret = 'client-secret-example'
        client = FakeClient({
            '/auth-files': {'files': [{'provider': 'codex', 'status': 'active', 'email': 'private@example.test',
                                       'name': 'private-file', 'account': 'private-account', 'path': '/private-path',
                                       'id_token': {'access_token': 'PRIVATE-TOKEN'}, 'status_message': 'PRIVATE-ERROR',
                                       'success': 5, 'failed': 1}]},
            '/usage': {'usage': {'total_requests': 7, 'success_count': 6, 'failure_count': 1, 'total_tokens': 90,
                                'apis': {secret: {'total_requests': 7, 'total_tokens': 90, 'models': {
                                    'model-a': {'total_requests': 7, 'total_tokens': 90, 'details': [{'source': 'PRIVATE-SOURCE'}]},
                                    secret: {'total_requests': 0, 'total_tokens': 0}}}},
                                'requests_by_day': {'2026-10-06': 7, secret: 99}}},
            '/api-key-usage': {'claude': {'https://private-endpoint|upstream-secret-example': {'success': 2, 'failed': 0}}}})
        result = backend.snapshot(client, False)
        output = json.dumps(result)
        for token in [secret, 'private-file', 'private-account', '/private-path', 'PRIVATE-',
                      'private-endpoint', 'upstream-secret-example', client.key]:
            self.assertNotIn(token, output)
        self.assertEqual(result['totalRequests'], 7)
        self.assertEqual(result['totalTokens'], 90)
        self.assertEqual(result['models'][0], {'name': 'model-a', 'requests': 7, 'tokens': 90})
        self.assertEqual(result['clients'][0]['name'], 'Client 1')
        self.assertEqual(result['history'], [{'label': '2026-10-06', 'requests': 7}])
        self.assertNotIn('/usage-queue', client.calls)

    def test_modern_counters_and_unknown_fields(self):
        client = FakeClient({
            '/auth-files': {'files': [
                {'provider': 'codex', 'status': 'active', 'success': 10, 'failed': 2,
                 'recent_requests': [{'time': '09:00', 'success': 2, 'failed': 1}]},
                {'provider': 'codex', 'account_type': 'api_key', 'account': 'upstream-secret', 'success': 100}]},
            '/api-key-usage': {'private-custom-provider': {'private-url|upstream-secret': {
                'success': 3, 'failed': 1, 'recent_requests': [{'time': '09:00', 'success': 1, 'failed': 0}]}}}})
        result = backend.snapshot(client, True)
        self.assertFalse(result['usageAvailable'])
        self.assertIsNone(result['totalTokens'])
        self.assertEqual(result['totalRequests'], 16)
        self.assertEqual(len(result['connections']), 2)
        self.assertEqual(result['connections'][1]['provider'], 'custom')
        self.assertEqual(result['history'], [{'label': '09:00', 'requests': 4}])
        self.assertEqual(result['metricsLabel'], 'Upstream attempts')
        self.assertNotIn('private-', json.dumps(result))

    def test_missing_counters_are_unknown_and_partial_counts_are_labelled(self):
        client = FakeClient({'/auth-files': {'files': [{'provider': 'codex'},
            {'provider': 'claude', 'success': 5, 'failed': 2}]}})
        result = backend.snapshot(client, False)
        self.assertIsNone(result['connections'][0]['success'])
        self.assertIsNone(result['connections'][0]['failed'])
        self.assertEqual(result['totalRequests'], 7)
        self.assertTrue(any('partial' in notice for notice in result['notices']))
        client.responses['/auth-files']['files'] = [{'provider': 'codex'}]
        result = backend.snapshot(client, False)
        self.assertIsNone(result['totalRequests'])
        self.assertIsNone(result['success'])
        self.assertIsNone(result['failed'])

    def test_recent_history_preserves_midnight_order(self):
        client = FakeClient({'/auth-files': {'files': [{'provider': 'codex',
            'recent_requests': [{'time': '23:50', 'success': 2}, {'time': '00:00', 'success': 3}]}]}})
        self.assertEqual([row['label'] for row in backend.snapshot(client, False)['history']], ['23:50', '00:00'])

    def test_absent_optional_endpoint_and_numeric_defenses(self):
        result = backend.snapshot(FakeClient({'/auth-files': {'files': [
            {'provider': ['invalid'], 'status': 'secret-status', 'success': -2, 'failed': float('nan')}]}}), False)
        self.assertIsNone(result['totalRequests'])
        self.assertEqual(result['connections'][0]['status'], 'unknown')
        self.assertTrue(any('API-key' in notice for notice in result['notices']))
        self.assertEqual(backend.number(True), 0)


class StorageTests(unittest.TestCase):
    def test_roundtrip_permissions_and_forget(self):
        with tempfile.TemporaryDirectory() as root:
            store = backend.CredentialStore(root)
            session = {'url': 'https://example.test/v0/management', 'key': 'test-management', 'remember': True}
            self.assertIsNone(store.load())
            store.save(session)
            path = Path(store.directory) / 'credentials.json'
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(store.load(), session)
            store.save({**session, 'key': 'replacement'})
            self.assertEqual(store.load()['key'], 'replacement')
            store.forget()
            self.assertIsNone(store.load())

    def test_symlink_file_and_directory_rejected(self):
        for directory_link in [False, True]:
            with self.subTest(directory_link=directory_link), tempfile.TemporaryDirectory() as root:
                target = Path(root) / 'target'
                store = backend.CredentialStore(root)
                directory = Path(store.directory)
                if directory_link:
                    target.mkdir(mode=0o700)
                    directory.symlink_to(target, target_is_directory=True)
                else:
                    directory.mkdir(mode=0o700)
                    target.write_text('untouched')
                    (directory / 'credentials.json').symlink_to(target)
                with self.assertRaises((OSError, backend.SafeError)):
                    store.save({'url': 'https://example.test', 'key': 'test-management'})
                with self.assertRaises((OSError, backend.SafeError)):
                    store.load()
                if not directory_link:
                    self.assertEqual(target.read_text(), 'untouched')

    def test_unsafe_permissions_and_malformed_load_are_safe(self):
        with tempfile.TemporaryDirectory() as root:
            store = backend.CredentialStore(root)
            directory = Path(store.directory)
            directory.mkdir(mode=0o700)
            path = directory / 'credentials.json'
            path.write_text('SECRET malformed json')
            for mode in [0o644, 0o600]:
                path.chmod(mode)
                output = backend.Bridge(store).startup()
                self.assertFalse(output['configured'])
                self.assertNotIn('SECRET', json.dumps(output))

    def test_failed_connect_preserves_session_and_saved_settings(self):
        class RejectClient:
            def __init__(self, url, key):
                pass

            def get(self, endpoint, optional=False):
                raise backend.SafeError('Management access was rejected.')

        with tempfile.TemporaryDirectory() as root:
            store = backend.CredentialStore(root)
            session = {'url': 'https://original.test/v0/management', 'key': 'original-key', 'remember': True}
            store.save(session)
            bridge = backend.Bridge(store, RejectClient)
            bridge.startup()
            for remember in (False, True):
                result = bridge.command({'op': 'connect', 'url': 'https://replacement.test',
                                         'key': 'replacement-key', 'remember': remember})
                self.assertEqual(result['type'], 'error')
                self.assertFalse(result['retryable'])
                self.assertTrue(result['configured'])
                self.assertEqual(bridge.session, session)
                self.assertEqual(store.load(), session)

    def test_session_only_and_explicit_remember(self):
        with tempfile.TemporaryDirectory() as root:
            store = backend.CredentialStore(root)
            factory = lambda url, key: FakeClient({'/auth-files': {'files': []}})
            bridge = backend.Bridge(store, factory)
            command = {'op': 'connect', 'url': 'https://example.test', 'key': 'test-management'}
            self.assertEqual(bridge.command(command)['type'], 'snapshot')
            self.assertIsNone(store.load())
            self.assertEqual(bridge.command({**command, 'remember': True})['type'], 'snapshot')
            self.assertIsNotNone(store.load())
            output = bridge.command({**command, 'remember': False})
            self.assertFalse(output['remember'])
            self.assertIsNone(store.load())
            self.assertFalse(bridge.command({'op': 'forget'})['configured'])


class ProtocolTests(unittest.TestCase):
    def test_real_stdio_protocol_does_not_expose_input(self):
        with tempfile.TemporaryDirectory() as root:
            commands = ['not-json', json.dumps({'op': 'connect', 'url': 'http://remote.test', 'key': 'PRIVATE-SECRET'}),
                        json.dumps({'op': 'forget'})]
            result = subprocess.run([sys.executable, str(Path(backend.__file__))],
                                    input='\n'.join(commands) + '\n', text=True, capture_output=True,
                                    env={**os.environ, 'XDG_CONFIG_HOME': root}, timeout=5)
            self.assertEqual(result.returncode, 0)
            outputs = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([item['type'] for item in outputs], ['state', 'error', 'error', 'state'])
            self.assertNotIn('PRIVATE', result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
