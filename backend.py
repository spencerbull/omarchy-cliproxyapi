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
        return self._request('GET', endpoint, optional=optional)

    def quota(self, target):
        family = target['provider']
        urls = {'codex': 'https://chatgpt.com/backend-api/wham/usage',
                'claude': 'https://api.anthropic.com/api/oauth/usage'}
        if family not in urls or not target.get('auth_index'):
            raise SafeError('Quota is unavailable for this account.')
        headers = {'Authorization': 'Bearer $TOKEN$', 'Content-Type': 'application/json',
                   'User-Agent': 'codex-cli/0.149.1' if family == 'codex' else 'claude-cli/2.1.280 (external, cli)'}
        if family == 'claude':
            headers['anthropic-beta'] = 'oauth-2025-04-20'
        elif target.get('account_id'):
            headers['Chatgpt-Account-Id'] = target['account_id']
        payload = {'auth_index': target['auth_index'], 'method': 'GET', 'url': urls[family], 'header': headers}
        return self._request('POST', '/api-call', payload=payload)

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
    return {'type': 'snapshot', 'url': client.url, 'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'usageAvailable': usage_available, 'metricsLabel': 'Requests' if usage_available else 'Upstream attempts',
            'totalRequests': total, 'success': success, 'failed': failed, 'totalTokens': tokens,
            'models': sorted(models.values(), key=lambda row: -row['requests']), 'clients': clients,
            'accounts': accounts, 'connections': connections, 'history': [{'label': label, 'requests': count}
                for label, count in (sorted(history.items()) if usage_available else history.items())],
            'notices': notices, 'remember': remember}


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
               'ultra', 'basic', 'premium', 'individual'}
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


def build_accounts(client, files, api_keys, usage, forbidden, observed_at=None):
    """Only a unique auth_index joins request history to an upstream account."""
    files = [row for row in files if isinstance(row, dict)]
    indexes = Counter(identity(row.get('auth_index')) for row in files)
    details_by_index = {}
    for api in mapping(usage.get('apis')).values():
        for name, model in mapping(mapping(api).get('models')).items():
            rows = mapping(model).get('details')
            if not isinstance(rows, list):
                continue
            for row in rows:
                row = mapping(row)
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
        if not rank and nonempty:
            # Approximate rank only; lastRequestAt remains null for bucket evidence.
            rank = anchor - (len(recent) - 1 - nonempty[-1][0]) * 600
        label = account_label(raw.get('email'), 'Account ' + str(len(accounts) + 1), forbidden) if kind != 'api_key' else 'API key ' + str(len(accounts) + 1)
        plan = plan_label(mapping(raw.get('id_token')).get('plan_type')) or plan_label(raw.get('plan_type'))
        supported = kind == 'oauth' and family in ('codex', 'claude') and unique
        accounts.append({'id': account_id, 'provider': family, 'label': label, 'kind': kind, 'plan': plan,
            'status': status, 'requests': requests, 'success': success, 'failed': failed,
            'tokens': tokens if token_known and not missing_tokens else None, 'lastRequestAt': last_request,
            'lastActivityLabel': last_window, 'lastActivityKind': activity, 'lastActivityRank': rank,
            'history': recent, 'models': sorted(models.values(), key=lambda model: -model['requests']),
            'nextRetryAt': iso_timestamp(raw.get('next_retry_after')), 'quotaSupported': supported,
            'metricsLabel': scope})
        if supported:
            upstream_id = identity(mapping(raw.get('id_token')).get('chatgpt_account_id'))
            if upstream_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', upstream_id):
                upstream_id = None
            targets[account_id] = {'provider': family, 'auth_index': index, 'account_id': upstream_id, 'plan': plan}
    for group, key, raw in api_entries:
        recent = account_history(raw.get('recent_requests'))
        active = [(i, row) for i, row in enumerate(recent) if row['requests'] > 0]
        success, failed = counter(raw.get('success')), counter(raw.get('failed'))
        accounts.append({'id': stable_id(client.url, ['api', group, key]), 'provider': provider(group),
            'label': 'API key ' + str(len(accounts) + 1), 'kind': 'api_key', 'plan': None, 'status': 'unknown',
            'requests': success + failed if success is not None and failed is not None else None,
            'success': success, 'failed': failed, 'tokens': None, 'lastRequestAt': None,
            'lastActivityLabel': active[-1][1]['label'] if active else None,
            'lastActivityKind': 'window' if active else 'none', 'lastActivityRank': anchor - (len(recent) - 1 - active[-1][0]) * 600 if active else 0,
            'history': recent, 'models': [], 'nextRetryAt': None, 'quotaSupported': False,
            'metricsLabel': 'Upstream attempts'})
    return accounts, targets


def quota_windows(envelope, family):
    if envelope.get('status_code') != 200:
        raise SafeError('The provider could not return quota for this account.')
    payload = envelope.get('body')
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, UnicodeError):
            raise SafeError('The provider returned an unsupported quota response.') from None
    if not isinstance(payload, dict):
        raise SafeError('The provider returned an unsupported quota response.')
    candidates = []
    if family == 'codex':
        for group, prefix in [('rate_limit', ''), ('code_review_rate_limit', 'Code review · ')]:
            info = mapping(payload.get(group))
            for key in ('primary_window', 'secondary_window'):
                row = mapping(info.get(key))
                duration = counter(row.get('limit_window_seconds'))
                label = {18000: '5 hours', 604800: 'Weekly'}.get(duration,
                        'Primary' if key == 'primary_window' else 'Secondary')
                reset = iso_timestamp(row.get('reset_at'), numeric=True)
                delay = counter(row.get('reset_after_seconds'))
                if reset is None and delay is not None and delay <= 366 * 86400:
                    reset = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delay)).isoformat()
                candidates.append((prefix + label, row.get('used_percent'), reset))
    else:
        for key, label in [('five_hour', '5 hours'), ('seven_day', 'Weekly'),
                           ('seven_day_opus', 'Opus weekly'), ('seven_day_sonnet', 'Sonnet weekly'),
                           ('seven_day_oauth_apps', 'OAuth apps weekly'), ('seven_day_cowork', 'Cowork weekly')]:
            row = mapping(payload.get(key))
            candidates.append((label, row.get('utilization'), iso_timestamp(row.get('resets_at'))))
    windows = []
    for label, percent, reset in candidates:
        if isinstance(percent, (int, float)) and not isinstance(percent, bool) and 0 <= percent <= 100 and math.isfinite(percent):
            windows.append({'label': label, 'usedPercent': percent, 'resetAt': reset})
    if not windows:
        raise SafeError('This provider response does not include supported quota windows.')
    return windows[:8], plan_label(payload.get('plan_type'))


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
                return self.fetch_quota(command.get('id'))
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


    def fetch_quota(self, account_id):
        # Do not echo arbitrary input (including accidentally pasted keys).
        valid_id = account_id if isinstance(account_id, str) and account_id in self.targets else ''
        target = self.targets.get(valid_id)
        result = {'type': 'quota', 'accountId': valid_id, 'windows': [], 'plan': target.get('plan') if target else None,
                  'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            if target is None or self.session is None:
                raise SafeError('Refresh accounts before requesting quota for a supported account.')
            client = self.client_factory(self.session['url'], self.session['key'])
            windows, plan = quota_windows(client.quota(target), target['provider'])
            result['windows'] = windows
            result['plan'] = plan or result['plan']
        except SafeError as error:
            result['error'] = str(error)
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
