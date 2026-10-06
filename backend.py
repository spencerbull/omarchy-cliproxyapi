#!/usr/bin/env python3
"""Read-only CLIProxyAPI bridge. JSON lines on stdin/stdout; secrets stay here."""
import datetime
import http.client
import ipaddress
import json
import math
import os
import secrets
import ssl
import stat
import sys
import time
from urllib.parse import urlsplit, urlunsplit

MAX_RESPONSE = 8 * 1024 * 1024
MAX_COMMAND = 65536
TIMEOUT = 8
PROVIDERS = {'codex', 'claude', 'gemini', 'antigravity', 'openai', 'vertex', 'xai',
             'meta', 'kimi', 'kimi-ai', 'qwen', 'iflow', 'aistudio', 'github-copilot',
             'copilot', 'openai-compatibility', 'interactions', 'devin'}


class SafeError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def normalize_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise SafeError('Enter a valid server URL.')
    value = value.strip()
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
        raise SafeError('The server URL contains unsupported characters.')
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise SafeError('Enter a valid server URL.') from None
    if parts.scheme not in ('https', 'http') or not host or parts.username is not None or parts.password is not None:
        raise SafeError('Use an HTTP or HTTPS URL without embedded credentials.')
    if parts.query or parts.fragment or '?' in value or '#' in value:
        raise SafeError('Remove query parameters and fragments from the server URL.')
    loopback = host.lower() == 'localhost'
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parts.scheme == 'http' and not loopback:
        raise SafeError('Remote servers require HTTPS. HTTP is allowed only for localhost or a loopback IP.')
    if port == 0:
        raise SafeError('Enter a valid server port.')
    path = parts.path.rstrip('/')
    if path.endswith('/management.html'):
        path = path[:-len('/management.html')]
    if path.endswith('/v0/management'):
        path = path[:-len('/v0/management')]
    if path.endswith('/v8/management'):
        path = path[:-len('/v8/management')]
    if '%' in path or any(p in ('.', '..') for p in path.split('/')):
        raise SafeError('Use a plain server base path without encoded or relative segments.')
    return urlunsplit((parts.scheme, parts.netloc, path + '/v0/management', '', ''))


def validate_key(key):
    if not isinstance(key, str) or not key or len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise SafeError('Enter a valid management key without spaces or control characters.')
    return key


class CredentialStore:
    """Private atomic storage; directory-fd operations never follow credential symlinks."""
    def __init__(self, root=None):
        if root is None:
            root = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
        self.directory = os.path.join(root, 'omarchy-cliproxyapi')

    def _open(self, create=False):
        if create:
            os.makedirs(os.path.dirname(self.directory), mode=0o700, exist_ok=True)
            try:
                os.mkdir(self.directory, 0o700)
            except FileExistsError:
                pass
        try:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            os.close(fd)
            raise SafeError('The credential directory is not owned by this user.')
        if create:
            os.fchmod(fd, 0o700)
        elif st.st_mode & 0o077:
            os.close(fd)
            raise SafeError('Saved credentials have unsafe directory permissions.')
        return fd

    @staticmethod
    def _check_target(fd):
        try:
            st = os.stat('credentials.json', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
            raise SafeError('Saved credentials have an unsafe file type, owner, or permissions.')
        return True

    def load(self):
        fd = self._open()
        if fd is None:
            return None
        try:
            if not self._check_target(fd):
                return None
            source = os.open('credentials.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(source, 'rb') as stream:
                st = os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
                    raise SafeError('Saved credentials could not be read safely.')
                raw = stream.read(MAX_COMMAND + 1)
            if len(raw) > MAX_COMMAND:
                raise SafeError('Saved credentials are too large.')
            value = json.loads(raw)
            return {'url': normalize_url(value['url']), 'key': validate_key(value['key']), 'remember': True}
        finally:
            os.close(fd)

    def save(self, session):
        fd = self._open(create=True)
        name = '.credentials-' + secrets.token_hex(12)
        try:
            self._check_target(fd)
            target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(target, 'w') as stream:
                json.dump({'url': session['url'], 'key': session['key']}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_target(fd)
            os.replace(name, 'credentials.json', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.close(fd)

    def forget(self):
        fd = self._open()
        if fd is None:
            return
        try:
            self._check_target(fd)
            try:
                os.unlink('credentials.json', dir_fd=fd)
                os.fsync(fd)
            except FileNotFoundError:
                pass
        finally:
            os.close(fd)


class Client:
    def __init__(self, url, key):
        self.url = normalize_url(url)
        self.key = validate_key(key)

    def get(self, endpoint, optional=False):
        parts = urlsplit(self.url)
        cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
        kwargs = {'timeout': TIMEOUT}
        if parts.scheme == 'https':
            kwargs['context'] = ssl.create_default_context()
        connection = cls(parts.hostname, parts.port, **kwargs)
        try:
            connection.request('GET', parts.path + endpoint, headers={
                'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json',
                'User-Agent': 'omarchy-cliproxyapi/1.0', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise SafeError('Management access was rejected. Check the key and remote management setting.')
            if 300 <= response.status < 400:
                raise SafeError('The server redirected the request. Enter its final HTTPS URL directly.')
            if response.status == 404 and optional:
                return None
            if response.status == 404:
                raise SafeError('Management API not found. Check the URL and server management settings.')
            if response.status != 200:
                raise SafeError('The management server returned an unexpected response.', response.status >= 500)
            length = response.getheader('Content-Length')
            if length and (not length.isdecimal() or int(length) > MAX_RESPONSE):
                raise SafeError('The management response exceeds the supported size.')
            # A read deadline also bounds servers that trickle a large response.
            deadline = time.monotonic() + TIMEOUT
            chunks, received = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SafeError('The management server response timed out.', True)
                raw_socket = getattr(getattr(response.fp, 'raw', None), '_sock', None)
                if raw_socket is not None:
                    raw_socket.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > MAX_RESPONSE:
                    raise SafeError('The management response exceeds the supported size.')
            body = b''.join(chunks)
            try:
                result = json.loads(body)
            except (ValueError, UnicodeError):
                raise SafeError('The management server did not return valid JSON.') from None
            if not isinstance(result, dict):
                raise SafeError('The management server returned an unsupported data format.')
            return result
        except ssl.SSLError:
            raise SafeError('TLS verification failed. Check the server certificate.') from None
        except (OSError, http.client.HTTPException):
            raise SafeError('Could not reach the management server. Check its URL and connection.', True) from None
        finally:
            connection.close()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, min(int(value), 2**53 - 1))


def counter(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return number(value)


def mapping(value):
    return value if isinstance(value, dict) else {}


def provider(value):
    return value if isinstance(value, str) and value in PROVIDERS else 'custom'


def clean_model(value, forbidden):
    if not isinstance(value, str) or not value or len(value) > 160:
        return 'Unknown model'
    if any(ord(c) < 32 for c in value) or any(secret and secret in value for secret in forbidden):
        return 'Unknown model'
    return value


def snapshot(client, remember):
    # This endpoint is the connection probe; stop immediately after any auth error.
    auth = client.get('/auth-files')
    legacy = client.get('/usage', optional=True)
    api_keys = client.get('/api-key-usage', optional=True)
    files = auth.get('files')
    if not isinstance(files, list):
        raise SafeError('The management server returned an unsupported credential format.')
    usage = mapping(legacy.get('usage')) if legacy else {}
    usage_available = bool(legacy is not None and isinstance(legacy.get('usage'), dict))
    notices = []
    connections = []
    history = {}
    forbidden = [client.key]
    for item in files:
        item = mapping(item)
        if item.get('account_type') == 'api_key' and isinstance(item.get('account'), str):
            forbidden.append(item['account'])
        if item.get('account_type') == 'api_key' and api_keys is not None:
            continue
        status = 'disabled' if item.get('disabled') else 'unavailable' if item.get('unavailable') else item.get('status')
        if status not in ('active', 'disabled', 'unavailable', 'error'):
            status = 'unknown'
        connections.append({'name': '', 'provider': provider(item.get('provider', item.get('type'))),
                            'status': status, 'success': counter(item.get('success')), 'failed': counter(item.get('failed'))})
        add_history(history, item.get('recent_requests'))
    for group, records in mapping(api_keys).items():
        for key, raw in mapping(records).items():
            if isinstance(key, str):
                forbidden.append(key.rsplit('|', 1)[-1])
            raw = mapping(raw)
            connections.append({'name': '', 'provider': provider(group), 'status': 'unknown',
                                'success': counter(raw.get('success')), 'failed': counter(raw.get('failed'))})
            add_history(history, raw.get('recent_requests'))
    for index, connection in enumerate(connections, 1):
        connection['name'] = 'Connection ' + str(index)
    clients, models = [], {}
    if usage_available:
        apis = mapping(usage.get('apis'))
        forbidden.extend(key for key in apis if isinstance(key, str))
        for index, raw in enumerate(apis.values(), 1):
            raw = mapping(raw)
            clients.append({'name': 'Client ' + str(index), 'requests': number(raw.get('total_requests')),
                            'tokens': number(raw.get('total_tokens'))})
            for name, value in mapping(raw.get('models')).items():
                name = clean_model(name, forbidden)
                value = mapping(value)
                model = models.setdefault(name, {'name': name, 'requests': 0, 'tokens': 0})
                model['requests'] += number(value.get('total_requests'))
                model['tokens'] += number(value.get('total_tokens'))
        total = number(usage.get('total_requests'))
        success, failed = number(usage.get('success_count')), number(usage.get('failure_count'))
        tokens = number(usage.get('total_tokens'))
        daily = mapping(usage.get('requests_by_day'))
        history = dict(sorted((day, number(count)) for day, count in daily.items()
                              if isinstance(day, str) and valid_day(day))[-30:])
        notices.append('Client connections identify separate API keys. Shared keys cannot distinguish individual agents.')
    else:
        known_success = [row['success'] for row in connections if row['success'] is not None]
        known_failed = [row['failed'] for row in connections if row['failed'] is not None]
        success = sum(known_success) if known_success else None
        failed = sum(known_failed) if known_failed else None
        total = success + failed if success is not None and failed is not None else None
        tokens = None
        if total is None:
            notices.append('Request counters are unavailable for these connections.')
        elif len(known_success) < len(connections) or len(known_failed) < len(connections):
            notices.append('Request counters cover only connections that report them; totals are partial.')
        notices.append('This server does not expose legacy token or model usage. Upstream attempts include retries and fallback attempts.')
    if api_keys is None:
        notices.append('API-key connection counters are unavailable on this server.')
    notices.append('Counters are server memory snapshots and may reset when the server restarts.')
    return {'type': 'snapshot', 'url': client.url, 'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'usageAvailable': usage_available, 'metricsLabel': 'Requests' if usage_available else 'Upstream attempts',
            'totalRequests': total, 'success': success, 'failed': failed, 'totalTokens': tokens,
            'models': sorted(models.values(), key=lambda row: -row['requests']), 'clients': clients,
            'connections': connections, 'history': [{'label': label, 'requests': count}
                for label, count in (sorted(history.items()) if usage_available else history.items())],
            'notices': notices, 'remember': remember}


def valid_day(value):
    try:
        return len(value) == 10 and datetime.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def add_history(history, rows):
    if not isinstance(rows, list):
        return
    for row in rows:
        row = mapping(row)
        label = row.get('time')
        if isinstance(label, str) and len(label) == 5 and label[2] == ':' and label[:2].isdigit() and label[3:].isdigit() and int(label[:2]) < 24 and int(label[3:]) < 60:
            history[label] = history.get(label, 0) + number(row.get('success')) + number(row.get('failed'))


class Bridge:
    def __init__(self, store=None, client_factory=Client):
        self.store = store or CredentialStore()
        self.client_factory = client_factory
        self.session = None

    def state(self):
        return {'type': 'state', 'configured': self.session is not None,
                'url': self.session['url'] if self.session else '',
                'remember': bool(self.session and self.session['remember'])}

    def startup(self):
        try:
            self.session = self.store.load()
            return self.state()
        except Exception:
            return {'type': 'state', 'configured': False, 'url': '', 'remember': False,
                    'message': 'Saved credentials could not be loaded safely. Enter the connection again.'}

    def command(self, command):
        try:
            if not isinstance(command, dict):
                raise SafeError('Expected a JSON command.')
            op = command.get('op')
            if op == 'forget':
                self.store.forget()
                self.session = None
                return self.state()
            if op == 'connect':
                new = {'url': normalize_url(command.get('url')), 'key': validate_key(command.get('key')),
                       'remember': command.get('remember') is True}
                result = snapshot(self.client_factory(new['url'], new['key']), new['remember'])
                if new['remember']:
                    self.store.save(new)
                else:
                    self.store.forget()
                self.session = new
                return result
            if op != 'refresh':
                raise SafeError('Unknown command.')
            if self.session is None:
                raise SafeError('Set up the server URL and management key first.')
            session = self.session
            return snapshot(self.client_factory(session['url'], session['key']), session['remember'])
        except SafeError as error:
            return {'type': 'error', 'message': str(error), 'configured': self.session is not None,
                    'retryable': error.retryable}
        except Exception:
            return {'type': 'error', 'message': 'The connection could not be processed safely.',
                    'configured': self.session is not None, 'retryable': False}


def main():
    bridge = Bridge()
    print(json.dumps(bridge.startup()), flush=True)
    while True:
        line = sys.stdin.buffer.readline(MAX_COMMAND + 1)
        if not line:
            break
        if len(line) > MAX_COMMAND:
            print(json.dumps({'type': 'error', 'message': 'The command is too large.',
                              'configured': bridge.session is not None, 'retryable': False}), flush=True)
            break
        try:
            command = json.loads(line)
        except (ValueError, UnicodeError):
            command = None
        print(json.dumps(bridge.command(command), allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
