import json
import tempfile
import unittest
from unittest.mock import patch
from test_backend import FakeClient, backend, server


def snapshot(files, usage=None, keys=None):
    client = FakeClient({'/auth-files': {'files': files, 'observed_at': '2026-10-06T00:04:00Z'},
                         '/usage': {'usage': usage} if usage is not None else None,
                         '/api-key-usage': keys})
    targets = {}
    return backend.snapshot(client, False, targets), targets


def detail(index, stamp, failed=False, tokens=10):
    return {'auth_index': index, 'timestamp': stamp, 'failed': failed, 'tokens': {'total_tokens': tokens}}


class AccountTests(unittest.TestCase):
    def test_unique_account_attribution_and_unmatched_details(self):
        files = [{'auth_index': 'one', 'provider': 'codex', 'account_type': 'oauth', 'email': 'alex@example.com'},
                 {'auth_index': 'two', 'provider': 'codex', 'account_type': 'oauth', 'email': 'sam@example.com'}]
        rows = [detail('one', '2026-10-05T23:10:00Z'), detail('two', '2026-10-05T23:20:00Z', True, 3),
                detail('one', '2026-10-05T23:30:00Z', False, 7), detail('unknown', '2026-10-05T23:40:00Z')]
        usage = {'total_requests': 99, 'apis': {'secret-client-key': {'total_requests': 99, 'models': {
            'codex-model': {'total_requests': 99, 'details': rows}}}}}
        result, targets = snapshot(files, usage)
        one, two = result['accounts']
        self.assertEqual((one['requests'], one['success'], one['failed'], one['tokens']), (2, 2, 0, 17))
        self.assertEqual((two['requests'], two['success'], two['failed'], two['tokens']), (1, 0, 1, 3))
        self.assertEqual(one['lastRequestAt'], '2026-10-05T23:30:00+00:00')
        self.assertEqual(one['lastActivityKind'], 'exact')
        self.assertEqual(one['models'], [{'name': 'codex-model', 'requests': 2, 'tokens': 17}])
        self.assertEqual(one['label'], 'alex@example.com')
        self.assertEqual(one['metricsLabel'], 'Recorded requests')
        self.assertEqual(len(targets), 2)
        reversed_result, _ = snapshot(list(reversed(files)), usage)
        self.assertEqual(one['id'], reversed_result['accounts'][1]['id'])
        output = json.dumps(result)
        self.assertNotIn('secret-client-key', output)
        self.assertNotIn('auth_index', output)

    def test_ambiguous_auth_index_never_attributed(self):
        files = [{'auth_index': 'duplicate', 'provider': 'codex', 'account_type': 'oauth'}] * 2
        usage = {'apis': {'key': {'models': {'model': {'details': [detail('duplicate', '2026-10-06T00:00:00Z')]}}}}}
        result, targets = snapshot(files, usage)
        self.assertFalse(targets)
        for account in result['accounts']:
            self.assertIsNone(account['requests'])
            self.assertIsNone(account['lastRequestAt'])
            self.assertEqual(account['models'], [])

    def test_real_bucket_ranges_and_timestamp_distinction(self):
        files = [{'provider': 'claude', 'account_type': 'oauth', 'auth_index': 'one',
                  'email': 'alex@example.com', 'success': 4, 'failed': 1,
                  'updated_at': '2026-10-06T00:04:00Z', 'last_refresh': '2026-10-06T00:03:00Z',
                  'recent_requests': [{'time': '23:50-00:00', 'success': 3, 'failed': 1},
                                      {'time': '00:00-00:10', 'success': 1, 'failed': 0}]}]
        result, _ = snapshot(files)
        account = result['accounts'][0]
        self.assertEqual([r['label'] for r in account['history']], ['23:50-00:00', '00:00-00:10'])
        self.assertEqual(account['lastActivityLabel'], '00:00-00:10')
        self.assertEqual(account['lastActivityKind'], 'window')
        self.assertIsNone(account['lastRequestAt'])
        self.assertEqual(account['history'][0]['failed'], 1)
        self.assertEqual(result['history'][0]['requests'], 4)
        self.assertIsNone(backend.bucket_label('25:00-25:10'))
        self.assertIsNone(backend.bucket_label('10:00-14:00'))

    def test_newer_bucket_supersedes_old_exact_history_without_losing_timestamp(self):
        files = [{'provider': 'codex', 'auth_index': 'one', 'recent_requests': [
            {'time': '23:50-00:00', 'success': 0}, {'time': '00:00-00:10', 'success': 1}]}]
        old = '2026-10-05T22:00:00Z'
        usage = {'apis': {'key': {'models': {'model': {'details': [detail('one', old)]}}}}}
        result, _ = snapshot(files, usage)
        account = result['accounts'][0]
        self.assertEqual(account['lastRequestAt'], '2026-10-05T22:00:00+00:00')
        self.assertEqual(account['lastActivityKind'], 'window')
        self.assertEqual(account['lastActivityLabel'], '00:00-00:10')
        self.assertGreater(account['lastActivityRank'], backend.datetime.datetime.fromisoformat(account['lastRequestAt']).timestamp())

    def test_exact_history_in_current_bucket_keeps_precision_and_ignores_server_timezone_label(self):
        files = [{'provider': 'codex', 'auth_index': 'one', 'recent_requests': [
            {'time': '18:50-19:00', 'success': 0}, {'time': '19:00-19:10', 'success': 1}]}]
        for stamp in ['2026-10-06T00:00:00Z', '2026-10-06T00:03:00Z']:
            usage = {'apis': {'key': {'models': {'model': {'details': [detail('one', stamp)]}}}}}
            result, _ = snapshot(files, usage)
            account = result['accounts'][0]
            self.assertEqual(account['lastActivityKind'], 'exact')
            self.assertEqual(account['lastActivityRank'], backend.datetime.datetime.fromisoformat(stamp).timestamp())

    def test_identity_plan_and_key_redaction(self):
        files = [{'provider': 'codex', 'account_type': 'oauth', 'auth_index': 'one',
                  'email': 'alex@example.com', 'name': 'PRIVATE-FILE', 'account': 'NEVER-ACCOUNT',
                  'id_token': {'plan_type': 'plus', 'chatgpt_account_id': 'PRIVATE-ACCOUNT-ID'},
                  'next_retry_after': '2026-10-06T00:10:00Z'},
                 {'provider': 'codex', 'account_type': 'api_key', 'auth_index': 'two',
                  'account': 'PROVIDER-KEY', 'email': 'fake-key@example.com'},
                 {'provider': 'claude', 'email': '<script>@example.com', 'name': 'PRIVATE-FILE-2'},
                 {'provider': 'codex', 'email': 'management-secret-example@example.com'}]
        result, targets = snapshot(files)
        one, two, three, four = result['accounts']
        self.assertEqual(one['label'], 'alex@example.com')
        self.assertEqual(one['plan'], 'plus')
        self.assertEqual(one['nextRetryAt'], '2026-10-06T00:10:00+00:00')
        self.assertEqual(targets[one['id']]['account_id'], 'PRIVATE-ACCOUNT-ID')
        self.assertEqual(two['kind'], 'api_key')
        self.assertTrue(two['label'].startswith('API key'))
        self.assertFalse(two['quotaSupported'])
        self.assertEqual(three['label'], 'Account 3')
        self.assertEqual(four['label'], 'Account 4')
        for secret in ['PRIVATE-', 'NEVER-', 'PROVIDER-KEY', 'fake-key', 'management-secret-example', '<script>']:
            self.assertNotIn(secret, json.dumps(result))

    def test_missing_zero_and_counter_precedence(self):
        files = [{'provider': 'codex', 'auth_index': 'one', 'success': 0, 'failed': 0},
                 {'provider': 'claude', 'auth_index': 'two'}]
        usage = {'apis': {'key': {'models': {'model': {'details': [detail('one', '2026-10-06T00:00:00Z')]}}}}}
        result, _ = snapshot(files, usage)
        one, two = result['accounts']
        self.assertEqual(one['requests'], 0)
        self.assertEqual(one['metricsLabel'], 'Upstream attempts')
        self.assertIsNone(two['requests'])
        self.assertEqual(two['lastActivityKind'], 'none')

    def test_meta_xai_identity_preserved_without_quota_probes(self):
        files = [{'provider': 'meta', 'account_type': 'oauth', 'auth_index': 'meta-one',
                  'email': 'alex@example.com', 'plan_type': 'promax'},
                 {'provider': 'xai', 'account_type': 'oauth', 'auth_index': 'xai-one',
                  'email': 'sam@example.com', 'plan_type': 'self-serve-business-prolite'}]
        result, targets = snapshot(files)
        self.assertEqual([row['provider'] for row in result['accounts']], ['meta', 'xai'])
        self.assertEqual([row['plan'] for row in result['accounts']], ['promax', 'self-serve-business-prolite'])
        self.assertFalse(result['accounts'][0]['quotaSupported'])
        self.assertTrue(result['accounts'][1]['quotaSupported'])
        self.assertEqual(len(targets), 1)

    def test_missing_detail_tokens_not_reported_as_partial_total(self):
        rows = [detail('one', '2026-10-06T00:00:00Z'), detail('one', '2026-10-06T00:01:00Z', tokens=None)]
        usage = {'apis': {'key': {'models': {'model': {'details': rows}}}}}
        result, _ = snapshot([{'provider': 'codex', 'auth_index': 'one'}], usage)
        account = result['accounts'][0]
        self.assertIsNone(account['tokens'])
        self.assertIsNone(account['models'][0]['tokens'])
        self.assertEqual(account['requests'], 2)

    def test_unmatched_api_key_file_not_dropped(self):
        result, _ = snapshot([{'provider': 'codex', 'account_type': 'api_key', 'account': 'OTHER-KEY',
                               'success': 2, 'failed': 1}], keys={'codex': {'endpoint|PRIVATE-KEY': {'success': 4, 'failed': 0}}})
        self.assertEqual(len(result['accounts']), 2)
        self.assertEqual(result['totalRequests'], 7)
        self.assertNotIn('OTHER-KEY', json.dumps(result))

    def test_api_key_accounts_not_duplicated_or_identified(self):
        result, _ = snapshot([{'provider': 'codex', 'account_type': 'api_key', 'account': 'PRIVATE-KEY'}],
                             keys={'codex': {'https://private.test|PRIVATE-KEY': {'success': 4, 'failed': 0}}})
        self.assertEqual(len(result['accounts']), 1)
        self.assertEqual(result['accounts'][0]['requests'], 4)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('private.test', json.dumps(result))


class QuotaTests(unittest.TestCase):
    def test_fixed_request_contract(self):
        client = backend.Client('https://example.test', 'management-example')
        for family in ('codex', 'claude'):
            with patch.object(client, '_request', return_value={}) as request:
                client.quota({'provider': family, 'auth_index': 'selected', 'account_id': 'account-123',
                              'url': 'https://malicious.test', 'method': 'DELETE'})
                args, kwargs = request.call_args
                self.assertEqual(args, ('POST', '/api-call'))
                payload = kwargs['payload']
                self.assertEqual(payload['method'], 'GET')
                self.assertEqual(payload['auth_index'], 'selected')
                self.assertEqual(payload['header']['Authorization'], 'Bearer $TOKEN$')
                self.assertNotIn('malicious', json.dumps(payload))
                self.assertNotIn('management-example', json.dumps(payload))
                expected = 'https://chatgpt.com/backend-api/wham/usage' if family == 'codex' else 'https://api.anthropic.com/api/oauth/usage'
                self.assertEqual(payload['url'], expected)
        with self.assertRaises(backend.SafeError):
            client.quota({'provider': 'custom', 'auth_index': 'selected'})

    def test_real_management_post_uses_token_placeholder(self):
        envelope = {'status_code': 200, 'body': json.dumps({'five_hour': {'utilization': 20}})}
        with server({'/v0/management/api-call': (200, envelope, {})}) as (url, calls):
            result = backend.Client(url, 'test-management').quota({'provider': 'claude', 'auth_index': 'selected'})
            self.assertEqual(result, envelope)
            path, authorization, payload = calls[0]
            self.assertEqual(path, '/v0/management/api-call')
            self.assertEqual(authorization, 'Bearer test-management')
            self.assertEqual(payload['header']['Authorization'], 'Bearer $TOKEN$')
            self.assertEqual(payload['method'], 'GET')
            self.assertNotIn('test-management', json.dumps(payload))

    def test_codex_windows_and_reset_timestamps(self):
        payload = {'plan_type': 'plus', 'rate_limit': {
            'primary_window': {'used_percent': 24.5, 'limit_window_seconds': 18000, 'reset_at': 1791245400},
            'secondary_window': {'used_percent': 91, 'limit_window_seconds': 604800, 'reset_after_seconds': 600}}}
        windows, plan = backend.quota_windows({'status_code': 200, 'body': json.dumps(payload)}, 'codex')
        self.assertEqual(plan, 'plus')
        self.assertEqual([row['label'] for row in windows], ['5 hours', 'Weekly'])
        self.assertEqual(windows[0]['usedPercent'], 24.5)
        self.assertIsNotNone(windows[0]['resetAt'])
        self.assertIsNotNone(windows[1]['resetAt'])

    def test_claude_windows_and_malformed_values(self):
        payload = {'five_hour': {'utilization': 0, 'resets_at': '2026-10-06T01:00:00Z'},
                   'seven_day': {'utilization': 100, 'resets_at': 'invalid'},
                   'seven_day_opus': {'utilization': -3}, 'seven_day_sonnet': {'utilization': 101},
                   'seven_day_oauth_apps': {'utilization': float('nan')}, 'secret': 'PRIVATE-KEY'}
        windows, plan = backend.quota_windows({'status_code': 200, 'body': payload}, 'claude')
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]['usedPercent'], 0)
        self.assertIsNone(windows[1]['resetAt'])
        self.assertIsNone(plan)
        for value in ['malformed', [], {'five_hour': {'utilization': True}}, {'five_hour': {'utilization': 10**300}}]:
            with self.assertRaises(backend.SafeError):
                backend.quota_windows({'status_code': 200, 'body': value}, 'claude')

    def test_fable5_scoped_window_prefers_active_and_uses_static_label(self):
        payload = {'limits': [
            {'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'Fable 5'}}, 'percent': 80},
            {'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'fable5'}}, 'percent': 23,
             'resets_at': '2026-10-10T00:00:00Z', 'is_active': True},
            {'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'PRIVATE-MODEL'}}, 'percent': 51,
             'is_active': True}], 'iguana_necktie': {'utilization': 99}}
        windows, _ = backend.quota_windows({'status_code': 200, 'body': payload}, 'claude')
        self.assertEqual({key: windows[0][key] for key in ('label', 'usedPercent', 'resetAt')},
                         {'label': 'Fable 5 weekly', 'usedPercent': 23, 'resetAt': '2026-10-10T00:00:00+00:00'})
        self.assertEqual(windows[1]['label'], 'Scoped limit 3 weekly')
        self.assertNotIn('PRIVATE', json.dumps(windows))

    def test_fable_legacy_fallback_and_malformed_scoped_limits(self):
        payload = {'limits': [{'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'fable'}},
                               'percent': 101, 'is_active': True},
                              {'kind': 'daily_scoped', 'scope': {'model': {'display_name': 'fable'}}, 'percent': 50}],
                   'iguana_necktie': {'utilization': 7, 'resets_at': '2026-10-10T00:00:00Z'}}
        windows, _ = backend.quota_windows({'status_code': 200, 'body': payload}, 'claude')
        self.assertEqual(windows[-1]['usedPercent'], 7)
        self.assertEqual(windows[-1]['label'], 'Fable 5 weekly')
        payload['limits'] = 'invalid'
        self.assertEqual(backend.quota_windows({'status_code': 200, 'body': payload}, 'claude')[0], [windows[-1]])

    def test_explicit_protocol_success_and_isolated_failures(self):
        class QuotaClient(FakeClient):
            quota_error = False
            calls_count = 0

            def __init__(self, url, key):
                super().__init__({'/auth-files': {'files': [{'provider': 'codex', 'account_type': 'oauth',
                                      'auth_index': 'one', 'email': 'alex@example.com'}]}})

            def quota(self, target):
                type(self).calls_count += 1
                if type(self).quota_error:
                    return {'status_code': 401, 'body': 'PRIVATE-PROVIDER-ERROR'}
                return {'status_code': 200, 'body': {'rate_limit': {'primary_window': {'used_percent': 35}}}}

        with tempfile.TemporaryDirectory() as root:
            bridge = backend.Bridge(backend.CredentialStore(root), QuotaClient)
            snap = bridge.command({'op': 'connect', 'url': 'https://example.test', 'key': 'management-example'})
            account = snap['accounts'][0]
            self.assertEqual(QuotaClient.calls_count, 0)
            good = bridge.command({'op': 'quota', 'id': account['id']})
            self.assertEqual(good['type'], 'quota')
            self.assertEqual(good['accountId'], account['id'])
            self.assertEqual(good['windows'][0]['usedPercent'], 35)
            QuotaClient.quota_error = True
            bad = bridge.command({'op': 'quota', 'id': account['id']})
            self.assertEqual(bad['type'], 'quota')
            self.assertIn('error', bad)
            self.assertNotIn('PRIVATE', json.dumps(bad))
            self.assertTrue(bridge.state()['configured'])
            missing = bridge.command({'op': 'quota', 'id': 'ACCIDENTALLY-PASTED-KEY'})
            self.assertEqual(missing['accountId'], '')
            self.assertNotIn('ACCIDENTALLY', json.dumps(missing))
            bridge.command({'op': 'forget'})
            self.assertEqual(bridge.targets, {})


if __name__ == '__main__':
    unittest.main()
