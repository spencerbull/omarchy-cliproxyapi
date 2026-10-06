"""Synthetic management API for native preview; never contacts a provider."""
import datetime
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def fixture():
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now.replace(minute=now.minute // 10 * 10, second=0, microsecond=0) - datetime.timedelta(minutes=190)
    volumes = [0, 0, 2, 3, 1, 8, 5, 2, 0, 4, 12, 7, 16, 9, 20, 31, 24, 18, 26, 12]
    accounts = []
    for index, (provider, email, total, errors, plan) in enumerate([
        ('codex', 'work@example.com', 1284, 2, 'pro'),
        ('claude', 'studio@example.com', 896, 3, 'max'),
        ('codex', 'personal@example.com', 312, 0, 'plus'),
        ('claude', 'lab@example.com', 42, 4, 'max'),
        ('meta', 'research@example.com', 0, 0, ''),
        ('xai', 'sandbox@example.com', 0, 0, ''),
    ]):
        rows = []
        for bucket, volume in enumerate(volumes):
            left = start + datetime.timedelta(minutes=bucket * 10)
            right = left + datetime.timedelta(minutes=10)
            amount = max(0, volume - index * 3) if index < 4 else 0
            if index == 3 and bucket > 15:
                amount = 0
            failed = 1 if amount and errors and bucket in (12, 15) else 0
            rows.append({'time': left.strftime('%H:%M') + '-' + right.strftime('%H:%M'), 'success': amount, 'failed': failed})
        accounts.append({'auth_index': 'demo-account-' + str(index), 'provider': provider,
            'email': email, 'account_type': 'oauth', 'status': 'active', 'unavailable': index == 3,
            'success': total - errors, 'failed': errors, 'recent_requests': rows,
            'name': 'synthetic-' + str(index) + '.json',
            'id_token': {'plan_type': plan, 'chatgpt_account_id': 'synthetic-org-' + str(index)},
            'next_retry_after': (now + datetime.timedelta(minutes=38)).isoformat() if index == 3 else None})
    return {'observed_at': now.isoformat(), 'files': accounts}


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if self.headers.get('Authorization') == 'Bearer demo-only':
            return True
        self.reply(401, {})
        return False

    def do_GET(self):
        if not self.authorized():
            return
        if self.path.startswith('/v0/management/auth-files/download?'):
            self.server.downloads += 1
            self.reply(200, {'dca_token': 'dca:synthetic-only', 'api_key': 'synthetic-discarded'})
        elif self.path == '/demo/stats':
            self.reply(200, {'quota_calls': self.server.quota_calls, 'downloads': self.server.downloads})
        elif self.path == '/v0/management/auth-files':
            self.reply(200, fixture())
        elif self.path == '/v0/management/api-key-usage':
            self.reply(200, {})
        else:
            self.reply(404, {})

    def do_POST(self):
        if not self.authorized():
            return
        if self.path == '/demo/fail-next':
            self.server.fail_next = True
            self.reply(200, {})
            return
        if self.path != '/v0/management/api-call':
            self.reply(404, {})
            return
        data = json.loads(self.rfile.read(min(8192, int(self.headers.get('Content-Length', '0')))))
        now = datetime.datetime.now(datetime.timezone.utc)
        self.server.quota_calls += 1
        if self.server.fail_next:
            self.server.fail_next = False
            self.reply(200, {'status_code': 429, 'header': {'Retry-After': ['600']}, 'body': '{}'})
            return
        url = data.get('url', '')
        if url == 'https://chatgpt.com/backend-api/wham/usage':
            is_work = data.get('auth_index') == 'demo-account-0'
            body = {'plan_type': 'pro' if is_work else 'plus', 'rate_limit': {
                'primary_window': {'used_percent': 28 if is_work else 61, 'limit_window_seconds': 18000, 'reset_at': (now + datetime.timedelta(hours=2, minutes=14)).timestamp()},
                'secondary_window': {'used_percent': 53 if is_work else 24, 'limit_window_seconds': 604800, 'reset_at': (now + datetime.timedelta(days=4, hours=8)).timestamp()}},
                'credits': {'balance': 1200 if is_work else 0, 'has_credits': is_work, 'unlimited': False}}
        elif url.startswith('https://chatgpt.com/backend-api/subscriptions?'):
            body = {'active_until': (now + datetime.timedelta(days=19)).isoformat()}
        elif url == 'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits':
            body = {'available_count': 2, 'applicable_available_count': 1, 'credits': [
                {'id':'synthetic-reset', 'reset_type':'codex_rate_limits', 'status':'available', 'expires_at':(now + datetime.timedelta(days=6)).isoformat(), 'applicable':True}]}
        elif url == 'https://api.anthropic.com/api/oauth/profile':
            body = {'account': {'has_claude_max':True, 'has_claude_pro':False}}
        elif url == 'https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1':
            body = {'cedar_ember': {'eligible':True, 'grants':[{'id':'synthetic-grant', 'resets_total':1, 'resets_left':1, 'usable_now':True, 'paused':False, 'ends_at':(now + datetime.timedelta(days=7)).isoformat()}]}}
        elif url == 'https://api.anthropic.com/api/oauth/usage':
            limited = data.get('auth_index') == 'demo-account-3'
            body = {'five_hour': {'utilization': 100 if limited else 12, 'resets_at': (now + datetime.timedelta(hours=3)).isoformat()},
                'seven_day': {'utilization': 94 if limited else 34, 'resets_at': (now + datetime.timedelta(days=2)).isoformat()},
                'limits': [{'kind':'weekly_scoped', 'percent': 0 if limited else 20, 'resets_at':(now + datetime.timedelta(days=2)).isoformat(), 'is_active':not limited, 'scope':{'model':{'display_name':'Fable 5'}}}],
                'extra_usage': {'is_enabled':False}}
        elif url == 'https://api.meta.ai/muse-code/key':
            body = {'api_key':'synthetic-minted-discarded', 'is_subs_active':True, 'subs_tier_name':'pro',
                'subs_usage': {'window':{'used_percent':17, 'window_duration_mins':300, 'resets_at':(now + datetime.timedelta(hours=1)).timestamp()},
                    'weekly':{'used_percent':39, 'resets_at':(now + datetime.timedelta(days=3)).timestamp()}}}
        elif url == 'https://cli-chat-proxy.grok.com/v1/user?include=subscription':
            body = {'subscriptionTier':'SUBSCRIPTION_TIER_SUPER_GROK_HEAVY'}
        elif url == 'https://cli-chat-proxy.grok.com/v1/settings':
            body = {'subscription_tier_display':'SuperGrok Heavy'}
        elif url.startswith('https://cli-chat-proxy.grok.com/v1/billing'):
            body = {'config': {'currentPeriod': {'type':'USAGE_PERIOD_TYPE_WEEKLY', 'end':(now + datetime.timedelta(days=5)).isoformat()},
                'creditUsagePercent':42, 'prepaidBalance':{'val':1250}}}
        else:
            self.reply(200, {'status_code':404, 'body':'{}'})
            return
        self.reply(200, {'status_code': 200, 'body': json.dumps(body)})


def create_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), DemoHandler)
    server.quota_calls = 0
    server.downloads = 0
    server.fail_next = False
    return server


if __name__ == '__main__':
    server = create_server()
    print(server.server_port, flush=True)
    server.serve_forever()
