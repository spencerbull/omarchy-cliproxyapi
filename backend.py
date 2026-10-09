#!/usr/bin/env python3
"""Read-only CLIProxyAPI bridge. JSON lines on stdin/stdout; secrets stay here."""
import datetime
import http.client
import hashlib
import re
from collections import Counter
import ipaddress
import json
import math
import os
import secrets
import ssl
import stat
import sys
import time
from urllib.parse import urlsplit, urlunsplit, quote

MAX_RESPONSE = 8 * 1024 * 1024
MAX_COMMAND = 65536
TIMEOUT = 8
PROVIDERS = {'codex', 'claude', 'gemini', 'antigravity', 'openai', 'vertex', 'xai',
             'meta', 'kimi', 'kimi-ai', 'qwen', 'iflow', 'aistudio', 'github-copilot',
             'copilot', 'openai-compatibility', 'interactions', 'devin'}


class SafeError(Exception):
    def __init__(self, message, retryable=False, auth_failed=False):
        super().__init__(message)
        self.retryable = retryable
        self.auth_failed = auth_failed
        self.retry_after = None


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
        return self._request('GET', endpoint, optional=optional)

    def quota(self, target):
        family = target['provider']
        urls = {'codex': 'https://chatgpt.com/backend-api/wham/usage',
                'claude': 'https://api.anthropic.com/api/oauth/usage',
                'xai': 'https://cli-chat-proxy.grok.com/v1/billing?format=credits'}
        if family not in urls or not target.get('auth_index'):
            raise SafeError('Quota is unavailable for this account.')
        headers = {'Authorization': 'Bearer $TOKEN$', 'Content-Type': 'application/json',
                   'User-Agent': 'codex-cli/0.149.1' if family == 'codex' else 'claude-cli/2.1.280 (external, cli)'}
        if family == 'claude':
            headers['anthropic-beta'] = 'oauth-2025-04-20'
        elif family == 'xai':
            headers.update({'x-xai-token-auth': 'xai-grok-cli', 'x-grok-client-version': '0.2.91',
                            'User-Agent': 'grok-pager/0.2.91 grok-shell/0.2.91'})
        elif target.get('account_id'):
            headers['Chatgpt-Account-Id'] = target['account_id']
        payload = {'auth_index': target['auth_index'], 'method': 'GET', 'url': urls[family], 'header': headers}
        return self._request('POST', '/api-call', payload=payload)

    def quota_extra(self, target, resource):
        family = target['provider']
        urls = {
            ('codex', 'subscription'): 'https://chatgpt.com/backend-api/subscriptions',
            ('codex', 'reset-credits'): 'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits',
            ('xai', 'monthly'): 'https://cli-chat-proxy.grok.com/v1/billing',
            ('xai', 'user'): 'https://cli-chat-proxy.grok.com/v1/user?include=subscription',
            ('xai', 'settings'): 'https://cli-chat-proxy.grok.com/v1/settings',
            ('claude', 'reset-grants'): 'https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1',
            ('claude', 'profile'): 'https://api.anthropic.com/api/oauth/profile',
        }
        url = urls.get((family, resource))
        if not url or not target.get('auth_index'):
            raise SafeError('This supplemental quota request is unsupported.')
        headers = {'Authorization': 'Bearer $TOKEN$', 'Accept': 'application/json'}
        if family == 'codex':
            headers['User-Agent'] = 'codex-cli/0.149.1'
            if target.get('account_id'):
                headers['Chatgpt-Account-Id'] = target['account_id']
            if resource == 'subscription':
                if not target.get('account_id'):
                    raise SafeError('Subscription renewal requires an account identifier.')
                url += '?account_id=' + quote(target['account_id'], safe='')
            else:
                headers.update({'OpenAI-Beta': 'codex-1', 'Originator': 'Codex Desktop'})
        elif family == 'claude':
            headers.update({'anthropic-beta': 'oauth-2025-04-20', 'Content-Type': 'application/json',
                            'User-Agent': 'claude-cli/2.1.280 (external, cli)'})
        else:
            headers.update({'x-xai-token-auth': 'xai-grok-cli', 'x-grok-client-version': '0.2.91',
                            'User-Agent': 'grok-pager/0.2.91 grok-shell/0.2.91'})
        return self._request('POST', '/api-call', payload={
            'auth_index': target['auth_index'], 'method': 'GET', 'url': url, 'header': headers})

    def meta_quota(self, target, allow_key_issue=False):
        # This route can mint a key. Consent is checked here as well as in Bridge.
        if allow_key_issue is not True:
            raise SafeError('Meta quota requires explicit consent because the provider may issue an API key.')
        if target.get('provider') != 'meta' or not valid_auth_filename(target.get('filename')):
            raise SafeError('Meta quota requires a supported account credential file.')
        credential = self.get('/auth-files/download?name=' + quote(target['filename'], safe=''))
        token = credential.get('dca_token')
        if not isinstance(token, str) or not re.fullmatch(r'dca:[A-Za-z0-9._~+/=-]{1,8192}', token):
            raise SafeError('This Meta account does not provide the required quota credential.')
        return self._request('POST', '/api-call', payload={
            'auth_index': target['auth_index'], 'method': 'POST',
            'url': 'https://api.meta.ai/muse-code/key', 'data': '{}',
            'header': {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                       'Content-Type': 'application/json', 'x-api-version': '1.0.0'}})

    def _request(self, method, endpoint, optional=False, payload=None):
        parts = urlsplit(self.url)
        cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
        kwargs = {'timeout': TIMEOUT}
        if parts.scheme == 'https':
            kwargs['context'] = ssl.create_default_context()
        connection = cls(parts.hostname, parts.port, **kwargs)
        try:
            connection.request(method, parts.path + endpoint,
                body=json.dumps(payload).encode() if payload is not None else None,
                headers={'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json',
                         'Content-Type': 'application/json',
                         'User-Agent': 'omarchy-cliproxyapi/1.0', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise SafeError('Management access was rejected. Check the key and remote management setting.', auth_failed=True)
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


def snapshot(client, remember, targets=None):
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
    api_secrets = {key.rsplit('|', 1)[-1] for records in mapping(api_keys).values()
                   for key in mapping(records) if isinstance(key, str)}
    for item in files:
        item = mapping(item)
        if item.get('account_type') == 'api_key' and isinstance(item.get('account'), str):
            forbidden.append(item['account'])
        if (item.get('account_type') == 'api_key' and isinstance(item.get('account'), str)
                and item['account'] in api_secrets):
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
    accounts, quota_targets = build_accounts(client, files, api_keys, usage, forbidden, auth.get('observed_at'))
    if targets is not None:
        targets.update(quota_targets)
    unattributed = sum(1 for _ in legacy_usage_records(usage)) - sum(row['usageRecords'] for row in accounts)
    collector = collector_usage(client, accounts, files)
    return {'type': 'snapshot', 'url': client.url, 'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'usageAvailable': usage_available, 'tokenUsageAvailable': usage_available,
            'usageSource': 'legacy' if usage_available else 'unavailable',
            'usageHistory': {'available': False, 'reason': 'Dated token history requires the updated usage collector.'},
            'usageUnattributedRecords': unattributed,
            'tokenSemantics': 'Reported legacy counters: cache and reasoning may overlap input/output; cache read/write cannot be separated, and totals follow the server accounting.',
            'metricsLabel': 'Requests' if usage_available else 'Upstream attempts',
            'totalRequests': total, 'success': success, 'failed': failed, 'totalTokens': tokens,
            'models': sorted(models.values(), key=lambda row: -row['requests']), 'clients': clients,
            'accounts': accounts, 'connections': connections, 'history': [{'label': label, 'requests': count}
                for label, count in (sorted(history.items()) if usage_available else history.items())],
            'notices': notices, 'remember': remember, **collector}


def valid_day(value):
    try:
        return len(value) == 10 and datetime.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def bucket_label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-2][0-9]:[0-5][0-9](?:-[0-2][0-9]:[0-5][0-9])?", value):
        return None
    if any(int(part[:2]) > 23 for part in value.split('-')):
        return None
    if '-' in value:
        start, end = [int(part[:2]) * 60 + int(part[3:]) for part in value.split('-')]
        if (end - start) % 1440 != 10:
            return None
    return value


def account_history(rows):
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[-20:]:
        row = mapping(row)
        label = bucket_label(row.get('time'))
        if label is None:
            continue
        success, failed = number(row.get('success')), number(row.get('failed'))
        result.append({'label': label, 'requests': success + failed, 'success': success, 'failed': failed})
    return result


def add_history(history, rows):
    for row in account_history(rows):
        history[row['label']] = history.get(row['label'], 0) + row['requests']


def iso_timestamp(value, numeric=False):
    try:
        if numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        elif isinstance(value, str) and len(value) <= 40:
            parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                return None
        else:
            return None
        if not 1970 <= parsed.year <= 9998:
            return None
        return parsed.astimezone(datetime.timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def plan_label(value):
    allowed = {'free', 'plus', 'pro', 'team', 'business', 'enterprise', 'edu', 'max', 'starter',
               'ultra', 'basic', 'premium', 'individual', 'promax', 'self-serve-business-prolite', 'self_serve_business_prolite', 'prolite', 'pro-lite', 'pro_lite'}
    return value.lower() if isinstance(value, str) and value.lower() in allowed else None


def account_label(value, fallback, forbidden):
    if (isinstance(value, str) and len(value) <= 254
            and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value)
            and not any(secret and secret in value for secret in forbidden)):
        return value
    return fallback


def identity(value):
    return value.strip() if isinstance(value, str) and value.strip() and len(value) <= 1024 else None


def stable_id(url, source):
    return hashlib.sha256(json.dumps([url, source], ensure_ascii=True).encode()).hexdigest()[:24]


LEGACY_TOKEN_FIELDS = {
    'total': 'total_tokens', 'input': 'input_tokens', 'output': 'output_tokens',
    'cached': 'cached_tokens', 'reasoning': 'reasoning_tokens',
    # The legacy snapshot does not distinguish read versus creation cache tokens.
    'cacheRead': None, 'cacheWrite': None,
}


def legacy_usage_records(usage):
    for api in mapping(usage.get('apis')).values():
        for name, model in mapping(mapping(api).get('models')).items():
            rows = mapping(model).get('details')
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        yield name, row


def legacy_token_metrics(matches):
    metrics = {}
    for name, field in LEGACY_TOKEN_FIELDS.items():
        values = []
        for _, row in matches:
            value = mapping(row.get('tokens')).get(field) if field else None
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and 0 <= value <= 2**53-1 and int(value) == value):
                values.append(int(value))
        metrics[name] = {'value': sum(values) if values else None,
                         'reported': len(values), 'records': len(matches)}
    return metrics


def build_accounts(client, files, api_keys, usage, forbidden, observed_at=None):
    """Only a unique auth_index joins request history to an upstream account."""
    files = [row for row in files if isinstance(row, dict)]
    indexes = Counter(identity(row.get('auth_index')) for row in files)
    details_by_index = {}
    for name, row in legacy_usage_records(usage):
        index = identity(row.get('auth_index'))
        if index and indexes[index] == 1:
            details_by_index.setdefault(index, []).append((clean_model(name, forbidden), row))
    accounts, targets = [], {}
    observed = iso_timestamp(observed_at)
    anchor = datetime.datetime.fromisoformat(observed).timestamp() if observed else time.time()
    anchor = math.floor(anchor / 600) * 600
    api_entries = [(group, key, mapping(raw)) for group, values in mapping(api_keys).items()
                   for key, raw in mapping(values).items()]
    api_secrets = {key.rsplit('|', 1)[-1] for _, key, _ in api_entries if isinstance(key, str)}
    for position, raw in enumerate(files):
        kind = raw.get('account_type') if raw.get('account_type') in ('oauth', 'api_key') else 'unknown'
        if kind == 'api_key' and isinstance(raw.get('account'), str) and raw['account'] in api_secrets:
            continue
        family = provider(raw.get('provider', raw.get('type')))
        index = identity(raw.get('auth_index'))
        unique = bool(index and indexes[index] == 1)
        fallback_identity = identity(raw.get('id')) or identity(raw.get('name')) or identity(raw.get('email'))
        source = ['auth', index] if unique else ['auth', family, fallback_identity or position]
        account_id = stable_id(client.url, source)
        status = 'disabled' if raw.get('disabled') else 'unavailable' if raw.get('unavailable') else raw.get('status')
        if status not in ('active', 'disabled', 'unavailable', 'error'):
            status = 'unknown'
        matches = details_by_index.get(index, []) if unique else []
        success, failed = counter(raw.get('success')), counter(raw.get('failed'))
        scope = 'Upstream attempts'
        if (success is None or failed is None) and matches:
            valid = [row for _, row in matches if isinstance(row.get('failed'), bool)]
            success = sum(not row['failed'] for row in valid) if valid else None
            failed = sum(row['failed'] for row in valid) if valid else None
            scope = 'Recorded requests'
        requests = success + failed if success is not None and failed is not None else None
        models, tokens, token_known, stamps = {}, 0, False, []
        missing_tokens = set()
        for name, row in matches:
            model = models.setdefault(name, {'name': name, 'requests': 0, 'tokens': None})
            model['requests'] += 1
            count = counter(mapping(row.get('tokens')).get('total_tokens'))
            if count is not None:
                model['tokens'] = (model['tokens'] or 0) + count
                tokens += count
                token_known = True
            else:
                missing_tokens.add(name)
            stamp = iso_timestamp(row.get('timestamp'))
            if stamp:
                stamps.append(stamp)
        for name in missing_tokens:
            models[name]['tokens'] = None
        last_request = max(stamps) if stamps else None
        recent = account_history(raw.get('recent_requests'))
        nonempty = [(i, row) for i, row in enumerate(recent) if row['requests'] > 0]
        last_window = nonempty[-1][1]['label'] if nonempty else None
        activity = 'exact' if last_request else 'window' if last_window else 'none'
        rank = datetime.datetime.fromisoformat(last_request).timestamp() if last_request else 0
        if nonempty:
            # Compare the bucket's UTC lower bound, inferred from its position.
            # Retain an exact timestamp inside that bucket instead of downgrading it.
            window_rank = anchor - (len(recent) - 1 - nonempty[-1][0]) * 600
            if not last_request or window_rank > rank:
                activity = 'window'
                rank = window_rank
        label = account_label(raw.get('email'), 'Account ' + str(len(accounts) + 1), forbidden) if kind != 'api_key' else 'API key ' + str(len(accounts) + 1)
        plan = plan_label(mapping(raw.get('id_token')).get('plan_type')) or plan_label(raw.get('plan_type'))
        meta_eligible = family == 'meta' and valid_auth_filename(raw.get('name')) and raw.get('runtime_only') not in (True, 'true')
        supported = kind == 'oauth' and unique and (family in ('codex', 'claude', 'xai') or meta_eligible)
        quota_reason = None if supported else quota_unavailable_reason(family, kind, unique)
        accounts.append({'id': account_id, 'provider': family, 'label': label, 'kind': kind, 'plan': plan,
            'status': status, 'requests': requests, 'success': success, 'failed': failed,
            'tokens': tokens if token_known and not missing_tokens else None, 'lastRequestAt': last_request,
            'tokenMetrics': legacy_token_metrics(matches), 'usageRecords': len(matches),
            'usageFirstAt': min(stamps) if stamps else None, 'usageLastAt': last_request,
            'lastActivityLabel': last_window, 'lastActivityKind': activity, 'lastActivityRank': rank,
            'history': recent, 'models': sorted(models.values(), key=lambda model: -model['requests']),
            'nextRetryAt': iso_timestamp(raw.get('next_retry_after')), 'quotaSupported': supported,
            'quotaConsentRequired': supported and family == 'meta', 'quotaReason': quota_reason,
            'metricsLabel': scope})
        if supported:
            upstream_id = identity(mapping(raw.get('id_token')).get('chatgpt_account_id'))
            if upstream_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', upstream_id):
                upstream_id = None
            targets[account_id] = {'provider': family, 'auth_index': index, 'account_id': upstream_id, 'plan': plan,
                'filename': raw.get('name') if meta_eligible else None,
                'renewalAt': quota_instant(mapping(raw.get('id_token')).get('chatgpt_subscription_active_until'))}
    for group, key, raw in api_entries:
        recent = account_history(raw.get('recent_requests'))
        active = [(i, row) for i, row in enumerate(recent) if row['requests'] > 0]
        success, failed = counter(raw.get('success')), counter(raw.get('failed'))
        accounts.append({'id': stable_id(client.url, ['api', group, key]), 'provider': provider(group),
            'label': 'API key ' + str(len(accounts) + 1), 'kind': 'api_key', 'plan': None, 'status': 'unknown',
            'requests': success + failed if success is not None and failed is not None else None,
            'success': success, 'failed': failed, 'tokens': None, 'lastRequestAt': None,
            'tokenMetrics': legacy_token_metrics([]), 'usageRecords': 0, 'usageFirstAt': None, 'usageLastAt': None,
            'lastActivityLabel': active[-1][1]['label'] if active else None,
            'lastActivityKind': 'window' if active else 'none', 'lastActivityRank': anchor - (len(recent) - 1 - active[-1][0]) * 600 if active else 0,
            'history': recent, 'models': [], 'nextRetryAt': None, 'quotaSupported': False,
            'quotaConsentRequired': False, 'quotaReason': 'Subscription quota is unavailable for API-key connections.',
            'metricsLabel': 'Upstream attempts'})
    return accounts, targets


COLLECTOR_ROUTE = '/plugins/omarchy-usage/summary'
COLLECTOR_SEMANTICS = ('SDK-reported tokens; cache and reasoning may overlap input/output. '
                       'Zero submetrics are normalized SDK values; all-zero records remain unknown.')


def usage_counter(value):
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 2**53-1 and int(value) == value):
        return int(value)
    return None


def collector_usage(client, accounts, files):
    """Read only our fixed, management-authenticated observer export. Never a queue."""
    try:
        payload = client.get(COLLECTOR_ROUTE, optional=True)
    except SafeError as error:
        if error.auth_failed:
            raise
        return {'collectorNotice': 'Collector could not be read. Showing server snapshot data.'}
    if payload is None:
        return {}
    if (not isinstance(payload, dict) or type(payload.get('schemaVersion')) is not int
            or payload.get('schemaVersion') not in (1, 2) or payload.get('source') != 'omarchy-usage'
            or not isinstance(payload.get('accounts'), list) or len(payload['accounts']) > 10000
            or not iso_timestamp(payload.get('startedAt'))
            or not isinstance(payload.get('partial'), bool) or payload.get('health') not in ('ok', 'degraded')):
        return {'collectorNotice': 'Collector format is unsupported. Showing server snapshot data.'}
    # Validate completely before replacing any legacy account data.
    rows, seen = [], set()
    for raw in payload['accounts']:
        raw = mapping(raw)
        index, family = identity(raw.get('authIndex')), provider(raw.get('provider'))
        count, failed = usage_counter(raw.get('requests')), usage_counter(raw.get('failed'))
        first_at, last_at = iso_timestamp(raw.get('firstRequestAt')), iso_timestamp(raw.get('lastRequestAt'))
        key = (index, family)
        if (not index or key in seen or count is None or failed is None or failed > count
                or (count and (not first_at or not last_at or first_at > last_at))):
            return {'collectorNotice': 'Collector data is incomplete or invalid. Showing server snapshot data.'}
        seen.add(key)
        metrics = {}
        for name in LEGACY_TOKEN_FIELDS:
            value = mapping(raw.get('tokenMetrics')).get(name)
            samples = usage_counter(mapping(raw.get('metricSamples')).get(name))
            if samples is None or samples > count or (value is not None and usage_counter(value) is None) or (samples > 0) != (value is not None):
                return {'collectorNotice': 'Collector metrics are invalid. Showing server snapshot data.'}
            metrics[name] = {'value': value, 'reported': samples, 'records': count}
        rows.append((index, family, count, failed, first_at, last_at, metrics))
    indexes = Counter(identity(mapping(row).get('auth_index')) for row in files)
    by_id = {a['id']: a for a in accounts}
    join = {}
    partial = payload['partial'] or payload['health'] != 'ok'
    for raw in files:
        raw = mapping(raw)
        index = identity(raw.get('auth_index'))
        if index and indexes[index] == 1:
            account = by_id.get(stable_id(client.url, ['auth', index]))
            if account:
                join[(index, account['provider'])] = account
    # Use one collection period for all accounts; do not mix lifetime attempts
    # into provider totals when some credentials have no collector observations.
    attributable_ids = {account['id'] for account in join.values()}
    for account in accounts:
        attributable = account['id'] in attributable_ids
        account.update({'usageSource': 'collector', 'usageRequests': 0 if attributable else None,
                        'usageFailed': 0 if attributable else None, 'usageRecords': 0,
                        'usageFirstAt': None, 'usageLastAt': None, 'usagePartial': partial,
                        'tokenMetrics': legacy_token_metrics([])})
    unmatched = 0
    for index, family, count, failed, first_at, last_at, metrics in rows:
        account = join.get((index, family))
        if account is None:
            unmatched += count
            continue
        account.update({'usageRequests': count, 'usageFailed': failed, 'usageRecords': count,
                        'usageFirstAt': first_at, 'usageLastAt': last_at, 'tokenMetrics': metrics})
        # Latest activity may come from a newer server bucket than this collector.
        if last_at and datetime.datetime.fromisoformat(last_at).timestamp() >= account['lastActivityRank']:
            account.update({'lastRequestAt': last_at, 'lastActivityKind': 'exact',
                            'lastActivityRank': datetime.datetime.fromisoformat(last_at).timestamp()})
    return {'usageSource': 'collector', 'tokenUsageAvailable': True,
            'usageSince': iso_timestamp(payload['startedAt']), 'usagePartial': partial,
            'usageUnattributedRecords': unmatched, 'tokenSemantics': COLLECTOR_SEMANTICS,
            'collectorNotice': 'Collector reports incomplete coverage. Totals may omit usage.' if partial else '',
            'usageHistory': collector_history(payload, join, client, files)}


def collector_history(payload, join, client, files):
    """Validate a bounded UTC-day export separately so bad history cannot hide lifetime usage."""
    unavailable = {'available': False, 'reason': 'Dated token history is invalid or unavailable.'}
    if payload.get('schemaVersion') == 1:
        return {'available': False, 'reason': 'Update the server usage collector to enable day and model totals.'}
    since = iso_timestamp(payload.get('historySince'))
    as_of = iso_timestamp(payload.get('asOf'))
    started = iso_timestamp(payload.get('startedAt'))
    if not since or not as_of or not started:
        return unavailable
    since_time, as_of_time, started_time = map(datetime.datetime.fromisoformat, (since, as_of, started))
    now = datetime.datetime.now(datetime.timezone.utc)
    if (since_time < started_time or since_time > as_of_time or started_time.year < 2020
            or as_of_time > now + datetime.timedelta(minutes=5)
            or not isinstance(payload.get('historyPartial'), bool)
            or usage_counter(payload.get('historyDropped')) is None
            or not isinstance(payload.get('buckets'), list) or len(payload['buckets']) > 2048):
        return unavailable
    first_day = as_of_time.date() - datetime.timedelta(days=29)
    forbidden = [client.key] + [mapping(row).get('account') for row in files
                               if mapping(row).get('account_type') == 'api_key'
                               and isinstance(mapping(row).get('account'), str)]
    rows, seen = [], set()
    totals = dict.fromkeys(LEGACY_TOKEN_FIELDS, 0)
    records_total = 0
    for raw in payload['buckets']:
        raw = mapping(raw)
        date, family, index, model = (raw.get(k) for k in ('date', 'provider', 'authIndex', 'model'))
        if (not isinstance(date, str) or not valid_day(date)
                or not first_day <= datetime.date.fromisoformat(date) <= as_of_time.date()
                or not isinstance(family, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', family)
                or not isinstance(index, str) or not re.fullmatch(r'[a-f0-9]{16}', index)
                or not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/()+-]{0,127}', model)
                or any(secret and secret in model for secret in forbidden)):
            return unavailable
        key = (date, family, index, model)
        count = usage_counter(raw.get('requests'))
        if key in seen or count is None or count == 0:
            return unavailable
        seen.add(key)
        records_total += count
        if usage_counter(records_total) is None:
            return unavailable
        metrics = {}
        for name in LEGACY_TOKEN_FIELDS:
            value = mapping(raw.get('tokenMetrics')).get(name)
            samples = usage_counter(mapping(raw.get('metricSamples')).get(name))
            if (samples is None or samples > count
                    or (value is not None and usage_counter(value) is None)
                    or (samples > 0) != (value is not None)):
                return unavailable
            totals[name] += value or 0
            if usage_counter(totals[name]) is None:
                return unavailable
            metrics[name] = {'value': value, 'reported': samples, 'records': count}
        family = provider(family)
        account = join.get((index, family))
        rows.append({'date': date, 'provider': family, 'accountId': account['id'] if account else None,
                     'model': model, 'usageRecords': count, 'tokenMetrics': metrics})
    return {'available': True, 'since': since, 'asOf': as_of,
            'partial': payload['historyPartial'] or payload['partial'] or payload['health'] != 'ok'
                       or payload['historyDropped'] > 0,
            'buckets': sorted(rows, key=lambda row: (row['date'], row['provider'], row['model'], row['accountId'] or ''))}


def valid_auth_filename(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.@+-]{1,255}', value)) and value not in ('.', '..')


def quota_unavailable_reason(family, kind, unique):
    if kind != 'oauth':
        return 'Subscription quota requires an OAuth account.'
    if not unique:
        return 'This account has no unique quota identifier.'
    if family == 'meta':
        return 'Meta quota requires a stored account file and may issue an API key.'
    if family in ('gemini', 'antigravity'):
        return 'This provider does not expose a supported quota route through this connection.'
    return 'No supported subscription quota route is available for this provider.'


def numeric(value, maximum=2**53-1):
    if isinstance(value, str) and len(value) <= 40 and re.fullmatch(r'\d+(?:\.\d+)?', value):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum:
        return None
    return value if math.isfinite(value) else None


def boolean(value):
    return value if isinstance(value, bool) else None


def first(row, *keys):
    return next((row[key] for key in keys if row.get(key) is not None), None)


def quota_instant(value):
    parsed = numeric(value)
    if parsed is not None:
        # Quota APIs use both epoch seconds and epoch milliseconds.
        return iso_timestamp(parsed / 1000 if parsed >= 100000000000 else parsed, numeric=True)
    return iso_timestamp(value)


def valid_percent(value):
    return numeric(value, 100) is not None


def quota_body(envelope):
    if envelope.get('status_code') != 200:
        error = SafeError('The provider could not return quota for this account.')
        if envelope.get('status_code') == 429:
            error = SafeError('The provider rate limited quota checks. Try again later.')
            headers = mapping(envelope.get('header'))
            for key, value in headers.items():
                if isinstance(key, str) and key.lower() == 'retry-after':
                    raw = value[0] if isinstance(value, list) and value else value
                    error.retry_after = numeric(raw, 86400)
        raise error
    payload = envelope.get('body')
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, UnicodeError):
            raise SafeError('The provider returned an unsupported quota response.') from None
    if not isinstance(payload, dict):
        raise SafeError('The provider returned an unsupported quota response.')
    return payload


def safe_feature(value, fallback):
    if not isinstance(value, str) or len(value) > 80:
        return fallback
    text = value.strip()
    # Accept recognizable product/model names, never arbitrary error/identity text.
    if re.fullmatch(r'(?i)(?:gpt|codex|claude|opus|sonnet|haiku|fable|spark|sora|grok|code review|image|deep research)(?:[ ._-][A-Za-z0-9 ._-]{0,65})?', text):
        return text
    return fallback


def window_label(seconds, fallback):
    if seconds == 18000:
        return '5 hours'
    if seconds == 604800:
        return 'Weekly'
    if seconds is not None and 28*86400 <= seconds <= 31*86400:
        return 'Monthly'
    if seconds and seconds % 3600 == 0:
        return str(int(seconds / 3600)) + ' hours'
    return fallback


def normalized_window(identifier, label, group, percent, reset, period=None, allowed=None, reached=None, active=None):
    return {'id': identifier, 'label': label, 'group': group,
            'usedPercent': numeric(percent, 100), 'resetAt': reset, 'periodSeconds': period,
            'allowed': boolean(allowed), 'limitReached': boolean(reached), 'isActive': boolean(active)}


def parse_reset_credits(payload):
    if not isinstance(payload, dict) or not any(key in payload for key in ('credits', 'available_count', 'availableCount', 'applicable_available_count', 'applicableAvailableCount')):
        raise SafeError('Manual reset availability is unavailable.')
    credits = []
    raw_credits = payload.get('credits')
    for raw in raw_credits if isinstance(raw_credits, list) else []:
        row = mapping(raw)
        if first(row, 'reset_type', 'resetType') != 'codex_rate_limits' or row.get('status') != 'available':
            continue
        expiry = quota_instant(first(row, 'expires_at', 'expiresAt'))
        if not expiry:
            continue
        credits.append({'expiresAt': expiry, 'applicable': boolean(first(row, 'is_applicable', 'isApplicable', 'applicable'))})
    available = numeric(first(payload, 'available_count', 'availableCount'))
    if available is None and isinstance(raw_credits, list):
        available = len(credits)
    return {'available': available,
            'applicable': numeric(first(payload, 'applicable_available_count', 'applicableAvailableCount')),
            'entries': sorted(credits, key=lambda row: row['expiresAt'])}


def claude_profile_plan(payload):
    organization = mapping(payload.get('organization'))
    if organization.get('organization_type') == 'claude_team' and organization.get('subscription_status') == 'active':
        return 'team'
    def flag(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value) if math.isfinite(value) else None
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ('true', '1', 'yes', 'y', 'on'):
                return True
            if text in ('false', '0', 'no', 'n', 'off'):
                return False
        return None
    account = mapping(payload.get('account'))
    maximum, pro = flag(account.get('has_claude_max')), flag(account.get('has_claude_pro'))
    if maximum:
        return 'max'
    if pro:
        return 'pro'
    return 'free' if maximum is False and pro is False else None


def parse_claude_reset_grants(payload, now=None):
    block = payload.get('cedar_ember')
    if not isinstance(block, dict) or not isinstance(block.get('eligible'), bool):
        raise SafeError('Claude manual reset availability is unavailable.')
    grants = block.get('grants', [])
    if not isinstance(grants, list):
        raise SafeError('Claude manual reset availability is unavailable.')
    now = time.time() if now is None else now
    def timestamp(row, key):
        raw = row.get(key)
        if raw is None:
            return None
        parsed = iso_timestamp(raw)
        if parsed is None:
            raise SafeError('Claude manual reset availability is unavailable.')
        return parsed
    def flag(row, key, default):
        raw = row.get(key)
        if raw is None:
            return default
        if not isinstance(raw, bool):
            raise SafeError('Claude manual reset availability is unavailable.')
        return raw
    def epoch(value):
        return datetime.datetime.fromisoformat(value).timestamp() if value else None
    cooldown = epoch(timestamp(block, 'cooldown_until'))
    timestamp(block, 'weekly_resets_at')
    at_limit = flag(block, 'at_limit', False)
    available, applicable, entries, seen = 0, 0, [], set()
    for raw in grants:
        row = mapping(raw)
        identifier, total, left = row.get('id'), row.get('resets_total'), row.get('resets_left')
        if (not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9_-]{1,40}', identifier)
                or identifier in seen or type(total) is not int or type(left) is not int
                or not 0 <= left <= total <= 2**53-1):
            raise SafeError('Claude manual reset availability is unavailable.')
        seen.add(identifier)
        if row.get('clears') is not None and not isinstance(row['clears'], list):
            raise SafeError('Claude manual reset availability is unavailable.')
        start, end = timestamp(row, 'starts_at'), timestamp(row, 'ends_at')
        paused, usable = flag(row, 'paused', False), flag(row, 'usable_now', False)
        requires_limit = flag(row, 'use_requires_limit', True)
        can_apply = (block['eligible'] and left > 0 and not paused and usable
                     and (not requires_limit or at_limit) and (cooldown is None or cooldown <= now)
                     and (start is None or epoch(start) <= now) and (end is None or epoch(end) > now))
        available += left
        applicable += left if can_apply else 0
        if left > 0 and end:
            entries.append({'expiresAt': end, 'applicable': bool(can_apply)})
    return {'available': available, 'applicable': applicable,
            'entries': sorted(entries, key=lambda row: row['expiresAt'])}


def quota_data(envelope, family):
    payload = quota_body(envelope)
    windows, notices = [], []
    result = {'windows': windows, 'plan': plan_label(first(payload, 'plan_type', 'planType')),
              'renewalAt': None, 'credits': None, 'resetCredits': [], 'extraUsage': None, 'notices': notices}
    if family == 'codex':
        groups = [('main', 'Subscription', mapping(first(payload, 'rate_limit', 'rateLimit'))),
                  ('review', 'Code review', mapping(first(payload, 'code_review_rate_limit', 'codeReviewRateLimit')))]
        additional = first(payload, 'additional_rate_limits', 'additionalRateLimits')
        for index, raw in enumerate(additional if isinstance(additional, list) else []):
            row = mapping(raw)
            label = safe_feature(first(row, 'limit_name', 'limitName', 'metered_feature', 'meteredFeature'), 'Additional limit ' + str(index+1))
            groups.append(('additional-' + str(index), label, mapping(first(row, 'rate_limit', 'rateLimit'))))
        for group_id, group_label, info in groups:
            for key, camel, fallback in [('primary_window', 'primaryWindow', 'Primary'), ('secondary_window', 'secondaryWindow', 'Secondary')]:
                raw = first(info, key, camel)
                if not isinstance(raw, dict):
                    continue
                period = numeric(first(raw, 'limit_window_seconds', 'limitWindowSeconds'), 366*86400)
                reset = quota_instant(first(raw, 'reset_at', 'resetAt'))
                delay = numeric(first(raw, 'reset_after_seconds', 'resetAfterSeconds'), 366*86400)
                if reset is None and delay is not None:
                    reset = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delay)).isoformat()
                percent = first(raw, 'used_percent', 'usedPercent')
                allowed = boolean(info.get('allowed'))
                reached = boolean(first(info, 'limit_reached', 'limitReached'))
                if numeric(percent, 100) is None and reset is None and allowed is None and reached is None:
                    continue
                label = window_label(period, fallback)
                if group_id != 'main':
                    label = group_label + ' · ' + label
                windows.append(normalized_window(group_id + '-' + key, label, group_label, percent, reset, period, allowed, reached))
        credit = mapping(payload.get('credits'))
        if credit:
            result['credits'] = {'balance': numeric(credit.get('balance')), 'unlimited': boolean(credit.get('unlimited')),
                                 'hasCredits': boolean(first(credit, 'has_credits', 'hasCredits')),
                                 'resetCreditsAvailable': None, 'resetCreditsApplicable': None}
        resets = first(payload, 'rate_limit_reset_credits', 'rateLimitResetCredits')
        if isinstance(resets, dict):
            try:
                apply_reset_credits(result, parse_reset_credits(resets))
            except SafeError:
                notices.append('Manual reset availability is unavailable.')
    elif family == 'claude':
        named = [('five_hour', '5 hours', 18000), ('seven_day', 'Weekly', 604800),
                 ('seven_day_opus', 'Opus weekly', 604800), ('seven_day_sonnet', 'Sonnet weekly', 604800),
                 ('seven_day_oauth_apps', 'OAuth apps weekly', 604800), ('seven_day_cowork', 'Cowork weekly', 604800)]
        known = {row[0] for row in named}
        for key in payload:
            if key not in known and isinstance(key, str) and re.fullmatch(r'seven_day_[a-z0-9_]{1,40}', key):
                suffix = key[len('seven_day_'):].replace('_', ' ')
                label = safe_feature(suffix, 'Additional') + ' weekly'
                named.append((key, label, 604800))
        for key, label, period in named:
            raw = payload.get(key)
            if not isinstance(raw, dict):
                continue
            percent, reset = raw.get('utilization'), quota_instant(raw.get('resets_at'))
            if numeric(percent, 100) is not None or reset:
                windows.append(normalized_window(key, label, 'Subscription', percent, reset, period))
        scoped = {}
        limits = payload.get('limits')
        for index, raw in enumerate(limits if isinstance(limits, list) else []):
            row = mapping(raw)
            kind = row.get('kind')
            periods = {'hourly_scoped': 3600, 'five_hour_scoped': 18000, 'daily_scoped': 86400,
                       'weekly_scoped': 604800, 'monthly_scoped': 30*86400}
            if not isinstance(kind, str) or kind not in periods:
                continue
            percent, reset = row.get('percent'), quota_instant(row.get('resets_at'))
            if numeric(percent, 100) is None and reset is None:
                continue
            model = mapping(mapping(row.get('scope')).get('model'))
            raw_name = first(model, 'display_name', 'id')
            normal = raw_name.strip().lower().replace(' ', '') if isinstance(raw_name, str) else ''
            name = 'Fable 5' if normal in ('fable', 'fable5') else safe_feature(raw_name, 'Scoped limit ' + str(index+1))
            group = identity(row.get('group')) or ''
            identity_key = (name, kind, group)
            # Provider may send superseded and active observations for one scope.
            if identity_key not in scoped or (row.get('is_active') is True and scoped[identity_key][1].get('is_active') is not True):
                scoped[identity_key] = (index, row, name, periods[kind])
        has_fable = False
        for index, row, name, period in scoped.values():
            has_fable = has_fable or (name == 'Fable 5' and period == 604800)
            suffix = {3600: 'hourly', 18000: '5 hours', 86400: 'daily', 604800: 'weekly', 30*86400: 'monthly'}[period]
            windows.append(normalized_window('scoped-' + str(index), name + ' ' + suffix, 'Model limits', row.get('percent'),
                quota_instant(row.get('resets_at')), period, active=row.get('is_active')))
        if not has_fable and isinstance(payload.get('iguana_necktie'), dict):
            row = payload['iguana_necktie']
            percent, reset = row.get('utilization'), quota_instant(row.get('resets_at'))
            if numeric(percent, 100) is not None or reset:
                windows.append(normalized_window('fable-legacy', 'Fable 5 weekly', 'Model limits', percent, reset, 604800))
        extra = payload.get('extra_usage')
        if isinstance(extra, dict):
            result['extraUsage'] = {'enabled': boolean(extra.get('is_enabled')),
                'monthlyLimit': numeric(extra.get('monthly_limit')), 'usedCredits': numeric(extra.get('used_credits')),
                'usedPercent': numeric(extra.get('utilization'), 100), 'unit': 'USD cents'}
    elif family == 'xai':
        config = payload.get('config')
        if not isinstance(config, dict):
            raise SafeError('xAI did not return subscription billing limits for this account.')
        current = mapping(first(config, 'currentPeriod', 'current_period'))
        weekly = current.get('type') == 'USAGE_PERIOD_TYPE_WEEKLY'
        percent = first(config, 'creditUsagePercent', 'credit_usage_percent')
        reset = quota_instant(current.get('end')) or quota_instant(first(config, 'billingPeriodEnd', 'billing_period_end'))
        if weekly or numeric(percent, 100) is not None:
            windows.append(normalized_window('xai-weekly', 'Weekly credits', 'Subscription', percent, reset, 604800))
        products = first(config, 'productUsage', 'product_usage')
        for index, raw in enumerate(products if isinstance(products, list) else []):
            row = mapping(raw)
            product = row.get('product')
            label = 'Grok Build' if product == 'GrokBuild' else safe_feature(product, 'Product ' + str(index+1))
            percent = first(row, 'usagePercent', 'usage_percent')
            if numeric(percent, 100) is not None:
                windows.append(normalized_window('xai-product-' + str(index), label, 'Product limits', percent, reset, 604800 if weekly else None))
        def cents(*keys):
            raw = first(config, *keys)
            return numeric(raw.get('val')) if isinstance(raw, dict) else numeric(raw)
        limit, used = cents('monthlyLimit', 'monthly_limit'), cents('used')
        if limit is not None or used is not None:
            monthly_percent = min(100, used / limit * 100) if limit and used is not None else None
            windows.append(normalized_window('xai-monthly', 'Monthly spending', 'Billing', monthly_percent,
                quota_instant(first(config, 'billingPeriodEnd', 'billing_period_end'))))
        result['extraUsage'] = {'enabled': None, 'monthlyLimit': limit, 'usedCredits': used,
            'usedPercent': min(100, used/limit*100) if limit and used is not None else None, 'unit': 'USD cents',
            'onDemandLimit': cents('onDemandCap', 'on_demand_cap'), 'onDemandUsed': cents('onDemandUsed', 'on_demand_used'),
            'prepaidBalance': cents('prepaidBalance', 'prepaid_balance')}
        if not windows and all(value is None for key, value in result['extraUsage'].items() if key != 'unit'):
            raise SafeError('xAI did not expose quota through its read-only billing endpoints. No inference probe was performed.')
        if any(window['usedPercent'] is None for window in windows):
            notices.append('xAI omitted usage percentages for some billing periods; unavailable usage is not zero.')
    elif family == 'meta':
        usage = mapping(payload.get('subs_usage'))
        result['plan'] = plan_label(payload.get('subs_tier_name')) or plan_label(usage.get('tier'))
        result['subscriptionActive'] = boolean(payload.get('is_subs_active'))
        for key, label, period in [('window', 'Current window', None), ('weekly', 'Weekly', 604800)]:
            row = mapping(usage.get(key))
            minutes = numeric(row.get('window_duration_mins'), 366*1440)
            if minutes is not None:
                period = minutes * 60
                label = window_label(period, label)
            windows.append(normalized_window('meta-' + key, label, 'Subscription', row.get('used_percent'),
                quota_instant(row.get('resets_at')), period))
        notices.append('Meta quota was read through a provider endpoint that may issue an API key.')
    else:
        raise SafeError('Quota is unavailable for this provider.')
    if len(windows) > 64:
        notices.append('The provider returned more than 64 windows; additional windows were omitted.')
        result['windows'] = windows[:64]
    if not windows and result['credits'] is None and result['extraUsage'] is None:
        raise SafeError('This provider response does not include supported quota windows.')
    return result


def apply_reset_credits(result, reset):
    if result['credits'] is None:
        result['credits'] = {'balance': None, 'unlimited': None, 'hasCredits': None,
                             'resetCreditsAvailable': None, 'resetCreditsApplicable': None}
    result['credits']['resetCreditsAvailable'] = reset['available']
    result['credits']['resetCreditsApplicable'] = reset['applicable']
    result['resetCredits'] = reset['entries']


def quota_windows(envelope, family):
    result = quota_data(envelope, family)
    return result['windows'], result['plan']


def xai_subscription_plan(user, settings):
    aliases = {'free': 'Free', 'grokfree': 'Free', 'supergrok': 'SuperGrok',
               'supergrokpro': 'SuperGrok Pro', 'supergrokheavy': 'SuperGrok Heavy',
               'xpremiumplus': 'X Premium+', 'xpremium': 'X Premium', 'grokpro': 'Grok Pro'}
    values = [first(settings, 'subscription_tier_display', 'subscriptionTierDisplay'),
              first(user, 'subscriptionTier', 'subscription_tier')]
    for raw in values:
        if isinstance(raw, str) and len(raw) <= 80:
            key = re.sub(r'[^a-z0-9]', '', raw.lower())
            if key in aliases:
                return aliases[key]
    return None


def fetch_xai_billing(client, target):
    observations, notices = [], []
    retry_after = None
    for resource in ('weekly', 'monthly'):
        try:
            envelope = client.quota(target) if resource == 'weekly' else client.quota_extra(target, 'monthly')
            observations.append(quota_data(envelope, 'xai'))
        except SafeError as error:
            if error.auth_failed:
                raise
            retry_after = max(retry_after or 0, error.retry_after or 0) or None
            notices.append('xAI ' + resource + ' billing limits are unavailable.')
        except Exception:
            notices.append('xAI ' + resource + ' billing limits are unavailable.')
    if not observations:
        error = SafeError('xAI did not expose limits through its read-only billing endpoints. No inference probe was performed.')
        error.retry_after = retry_after
        raise error
    result = observations[0]
    for additional in observations[1:]:
        existing = {row['id']: row for row in result['windows']}
        for row in additional['windows']:
            if row['id'] not in existing:
                result['windows'].append(row)
            elif existing[row['id']]['usedPercent'] is None and row['usedPercent'] is not None:
                result['windows'][result['windows'].index(existing[row['id']])] = row
        extra = additional['extraUsage']
        if extra and any(value is not None for key,value in extra.items() if key not in ('enabled','unit')):
            result['extraUsage'] = extra
        result['notices'].extend(additional['notices'])
    profile = {}
    for resource in ('user', 'settings'):
        try:
            profile[resource] = quota_body(client.quota_extra(target, resource))
        except SafeError as error:
            if error.auth_failed:
                raise
            retry_after = max(retry_after or 0, error.retry_after or 0) or None
        except Exception:
            pass
    result['plan'] = xai_subscription_plan(profile.get('user', {}), profile.get('settings', {}))
    if result['plan'] is None:
        notices.append('xAI subscription plan is unavailable.')
    result['notices'] = list(dict.fromkeys(result['notices'] + notices))
    if retry_after is not None:
        result['retryAfter'] = retry_after
    return result


class Bridge:
    def __init__(self, store=None, client_factory=Client):
        self.store = store or CredentialStore()
        self.client_factory = client_factory
        self.session = None
        self.targets = {}

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
                self.targets = {}
                return self.state()
            if op == 'connect':
                new = {'url': normalize_url(command.get('url')), 'key': validate_key(command.get('key')),
                       'remember': command.get('remember') is True}
                targets = {}
                result = snapshot(self.client_factory(new['url'], new['key']), new['remember'], targets)
                if new['remember']:
                    self.store.save(new)
                else:
                    self.store.forget()
                self.session = new
                self.targets = targets
                return result
            if op == 'quota':
                return self.fetch_quota(command.get('id'), command.get('allowKeyIssue') is True)
            if op != 'refresh':
                raise SafeError('Unknown command.')
            if self.session is None:
                raise SafeError('Set up the server URL and management key first.')
            session = self.session
            targets = {}
            result = snapshot(self.client_factory(session['url'], session['key']), session['remember'], targets)
            self.targets = targets
            return result
        except SafeError as error:
            return {'type': 'error', 'message': str(error), 'configured': self.session is not None,
                    'retryable': error.retryable}
        except Exception:
            return {'type': 'error', 'message': 'The connection could not be processed safely.',
                    'configured': self.session is not None, 'retryable': False}


    def fetch_quota(self, account_id, allow_key_issue=False):
        valid_id = account_id if isinstance(account_id, str) and account_id in self.targets else ''
        target = self.targets.get(valid_id)
        result = {'type': 'quota', 'accountId': valid_id, 'windows': [], 'plan': target.get('plan') if target else None,
                  'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'renewalAt': target.get('renewalAt') if target else None, 'credits': None,
                  'resetCredits': [], 'extraUsage': None, 'notices': []}
        try:
            if target is None or self.session is None:
                raise SafeError('Refresh accounts before requesting quota for a supported account.')
            if target['provider'] == 'meta' and allow_key_issue is not True:
                result['consentRequired'] = True
                raise SafeError('Meta quota requires explicit consent because the provider may issue an API key.')
            client = self.client_factory(self.session['url'], self.session['key'])
            family = target['provider']
            if family == 'xai':
                result.update(fetch_xai_billing(client, target))
            else:
                envelope = client.meta_quota(target, allow_key_issue=True) if family == 'meta' else client.quota(target)
                result.update(quota_data(envelope, family))
            result['plan'] = result['plan'] or target.get('plan')
            result['renewalAt'] = result['renewalAt'] or target.get('renewalAt')
            if family == 'codex':
                if target.get('account_id'):
                    try:
                        subscription = quota_body(client.quota_extra(target, 'subscription'))
                        result['renewalAt'] = quota_instant(first(subscription, 'active_until', 'activeUntil')) or result['renewalAt']
                        if result['renewalAt'] is None:
                            result['notices'].append('Subscription renewal time is unavailable.')
                    except SafeError as error:
                        if error.auth_failed:
                            raise
                        if error.retry_after is not None:
                            result['retryAfter'] = max(result.get('retryAfter', 0), error.retry_after)
                        result['notices'].append('Subscription renewal time is unavailable.')
                    except Exception:
                        result['notices'].append('Subscription renewal time is unavailable.')
                else:
                    result['notices'].append('Live subscription renewal requires an account identifier.')
                try:
                    apply_reset_credits(result, parse_reset_credits(quota_body(client.quota_extra(target, 'reset-credits'))))
                except SafeError as error:
                    if error.auth_failed:
                        raise
                    if error.retry_after is not None:
                        result['retryAfter'] = max(result.get('retryAfter', 0), error.retry_after)
                    result['notices'].append('Manual reset availability is unavailable.')
                except Exception:
                    result['notices'].append('Manual reset availability is unavailable.')

            elif family == 'claude':
                for resource, notice in [('profile', 'Claude subscription plan is unavailable.'),
                                         ('reset-grants', 'Claude manual reset availability is unavailable.')]:
                    try:
                        payload = quota_body(client.quota_extra(target, resource))
                        if resource == 'profile':
                            plan = claude_profile_plan(payload)
                            if plan:
                                result['plan'] = plan
                            else:
                                result['notices'].append(notice)
                        else:
                            apply_reset_credits(result, parse_claude_reset_grants(payload))
                    except SafeError as error:
                        if error.auth_failed:
                            raise
                        if error.retry_after is not None:
                            result['retryAfter'] = max(result.get('retryAfter', 0), error.retry_after)
                        result['notices'].append(notice)
                    except Exception:
                        result['notices'].append(notice)

        except SafeError as error:
            result['error'] = str(error)
            if error.auth_failed:
                result['authFailed'] = True
            if error.retry_after is not None:
                result['retryAfter'] = max(result.get('retryAfter', 0), error.retry_after)
        except Exception:
            result['error'] = 'The quota request could not be processed safely.'
        return result


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
