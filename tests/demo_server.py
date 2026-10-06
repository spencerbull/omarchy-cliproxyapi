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
            'id_token': {'plan_type': plan},
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
        if self.path == '/v0/management/auth-files':
            self.reply(200, fixture())
        elif self.path == '/v0/management/api-key-usage':
            self.reply(200, {})
        else:
            self.reply(404, {})

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != '/v0/management/api-call':
            self.reply(404, {})
            return
        data = json.loads(self.rfile.read(min(8192, int(self.headers.get('Content-Length', '0')))))
        now = datetime.datetime.now(datetime.timezone.utc)
        if data.get('auth_index') == 'demo-account-3':
            self.reply(200, {'status_code': 429, 'body': '{}'})
            return
        if data.get('url') == 'https://chatgpt.com/backend-api/wham/usage':
            body = {'plan_type': 'pro', 'rate_limit': {
                'primary_window': {'used_percent': 28, 'limit_window_seconds': 18000, 'reset_at': (now + datetime.timedelta(hours=2, minutes=14)).timestamp()},
                'secondary_window': {'used_percent': 53, 'limit_window_seconds': 604800, 'reset_at': (now + datetime.timedelta(days=4, hours=8)).timestamp()}}}
        else:
            body = {'five_hour': {'utilization': 12, 'resets_at': (now + datetime.timedelta(hours=3)).isoformat()},
                'seven_day': {'utilization': 34, 'resets_at': (now + datetime.timedelta(days=2)).isoformat()}}
        self.reply(200, {'status_code': 200, 'body': json.dumps(body)})


def create_server():
    return ThreadingHTTPServer(('127.0.0.1', 0), DemoHandler)


if __name__ == '__main__':
    server = create_server()
    print(server.server_port, flush=True)
    server.serve_forever()
