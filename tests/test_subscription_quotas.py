import json
import tempfile
import unittest
from unittest.mock import patch
from test_backend import backend, FakeClient, server
from test_accounts import snapshot


def envelope(payload, status=200):
    return {'status_code': status, 'body': json.dumps(payload)}


def codex_window(percent=20, duration=18000):
    return {'used_percent': percent, 'limit_window_seconds': duration, 'reset_at': 1791248400}


class CompleteQuotaTests(unittest.TestCase):
    def test_all_codex_groups_camel_case_metadata_and_more_than_eight(self):
        payload = {'planType': 'promax', 'rateLimit': {
            'allowed': False, 'limitReached': True,
            'primaryWindow': {'resetAt': '1791248400000', 'limitWindowSeconds': '604800'},
            'secondaryWindow': {'usedPercent': '17.5', 'limitWindowSeconds': '2592000'}},
            'code_review_rate_limit': {'primary_window': codex_window(22), 'secondary_window': codex_window(13, 604800)},
            'additional_rate_limits': [
                {'limit_name': 'gpt-5.3-codex-spark', 'rate_limit': {'primary_window': codex_window(3), 'secondary_window': codex_window(8, 604800)}},
                {'meteredFeature': 'PRIVATE-KEY', 'rateLimit': {'primaryWindow': codex_window(4)}},
                {'limit_name': 'image generation', 'rate_limit': {'primary_window': codex_window(5), 'secondary_window': codex_window(6)}}],
            'credits': {'balance': '12.50', 'unlimited': False, 'has_credits': True}}
        result = backend.quota_data(envelope(payload), 'codex')
        self.assertEqual(len(result['windows']), 9)
        first = result['windows'][0]
        self.assertEqual(first['label'], 'Weekly')
        self.assertIsNone(first['usedPercent'])
        self.assertFalse(first['allowed'])
        self.assertTrue(first['limitReached'])
        self.assertEqual(first['periodSeconds'], 604800)
        self.assertIsNotNone(first['resetAt'])
        self.assertEqual(result['windows'][1]['label'], 'Monthly')
        self.assertEqual(result['windows'][1]['usedPercent'], 17.5)
        self.assertEqual(result['windows'][4]['label'], 'gpt-5.3-codex-spark · 5 hours')
        self.assertEqual(result['credits']['balance'], 12.5)
        self.assertNotIn('PRIVATE-KEY', json.dumps(result))

    def test_claude_all_scopes_new_named_windows_and_extra_usage(self):
        payload = {'five_hour': {'utilization': 0, 'resets_at': '2026-10-06T20:00:00Z'},
                   'seven_day': {'utilization': 12}, 'seven_day_opus': {'utilization': 10},
                   'seven_day_fable_5': {'utilization': 30},
                   'limits': [
                       {'kind': 'daily_scoped', 'scope': {'model': {'display_name': 'Claude Sonnet 5'}}, 'percent': 15, 'is_active': True},
                       {'kind': 'monthly_scoped', 'scope': {'model': {'display_name': 'fable5'}}, 'percent': 17},
                       {'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'fable5'}}, 'percent': 20, 'is_active': True},
                       {'kind': [], 'percent': 10}],
                   'extra_usage': {'is_enabled': True, 'monthly_limit': 5000, 'used_credits': 1250, 'utilization': 25}}
        result = backend.quota_data(envelope(payload), 'claude')
        self.assertEqual(len(result['windows']), 7)
        self.assertEqual(result['extraUsage'], {'enabled': True, 'monthlyLimit': 5000, 'usedCredits': 1250,
                                             'usedPercent': 25, 'unit': 'USD cents'})
        self.assertTrue(any(row['periodSeconds'] == 86400 for row in result['windows']))
        self.assertTrue(any(row['label'] == 'Fable 5 monthly' for row in result['windows']))
        self.assertTrue(any(row['label'] == 'Fable 5 weekly' for row in result['windows']))

    def test_credits_and_extra_only_are_valid_not_empty_errors(self):
        result = backend.quota_data(envelope({'credits': {'unlimited': True, 'balance': '0'}}), 'codex')
        self.assertEqual(result['windows'], [])
        self.assertTrue(result['credits']['unlimited'])
        self.assertEqual(result['credits']['balance'], 0)
        result = backend.quota_data(envelope({'extra_usage': {'is_enabled': False}}), 'claude')
        self.assertFalse(result['extraUsage']['enabled'])
        self.assertIsNone(result['extraUsage']['monthlyLimit'])

    def test_reset_credit_expiry_counts_and_secret_ids_removed(self):
        payload = {'available_count': '2', 'applicable_available_count': 1, 'credits': [
            {'id': 'PRIVATE-CREDIT-ID', 'status': 'available', 'reset_type': 'codex_rate_limits',
             'expires_at': '2026-10-10T00:00:00Z', 'is_applicable': True},
            {'id': 'ANOTHER-PRIVATE-ID', 'status': 'available', 'reset_type': 'codex_rate_limits',
             'expires_at': '2026-10-11T00:00:00Z'},
            {'status': 'consumed', 'reset_type': 'codex_rate_limits', 'expires_at': '2026-10-12T00:00:00Z'},
            {'status': 'available', 'reset_type': 'different_type', 'expires_at': '2026-10-12T00:00:00Z'}]}
        parsed = backend.parse_reset_credits(payload)
        self.assertEqual(parsed['available'], 2)
        self.assertEqual(parsed['applicable'], 1)
        self.assertEqual(len(parsed['entries']), 2)
        self.assertTrue(parsed['entries'][0]['applicable'])
        self.assertIsNone(parsed['entries'][1]['applicable'])
        self.assertNotIn('PRIVATE', json.dumps(parsed))

    def test_window_bound_is_explicit_and_labels_do_not_echo_secrets(self):
        payload = {'additional_rate_limits': [{'limit_name': 'sk-PRIVATE-SECRET', 'rate_limit': {
            'primary_window': codex_window()}} for _ in range(70)]}
        result = backend.quota_data(envelope(payload), 'codex')
        self.assertEqual(len(result['windows']), 64)
        self.assertIn('64', result['notices'][0])
        self.assertNotIn('PRIVATE', json.dumps(result))


class QuotaTransportTests(unittest.TestCase):
    def test_codex_supplemental_routes_read_only_and_fixed(self):
        client = backend.Client('https://example.test', 'management-example')
        target = {'provider': 'codex', 'auth_index': 'idx', 'account_id': 'account-123'}
        with patch.object(client, '_request', return_value={}) as request:
            client.quota_extra(target, 'subscription')
            payload = request.call_args.kwargs['payload']
            self.assertEqual(payload['url'], 'https://chatgpt.com/backend-api/subscriptions?account_id=account-123')
            self.assertEqual(payload['method'], 'GET')
            client.quota_extra(target, 'reset-credits')
            payload = request.call_args.kwargs['payload']
            self.assertEqual(payload['url'], 'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits')
            self.assertEqual(payload['method'], 'GET')
            self.assertEqual(payload['header']['Authorization'], 'Bearer $TOKEN$')
            with self.assertRaises(backend.SafeError):
                client.quota_extra(target, 'consume')
            self.assertEqual(request.call_count, 2)

    def test_xai_failed_weekly_still_reads_monthly_without_inference(self):
        class XaiClient:
            calls = []
            def quota(self, target):
                self.calls.append('weekly')
                return envelope({'error': 'PRIVATE'}, 403)
            def quota_extra(self, target, resource):
                self.calls.append(resource)
                return envelope({'config': {'monthlyLimit': {'val': 10000}, 'used': {'val': 1500},
                                             'billingPeriodEnd': '2026-11-01T00:00:00Z'}})
        client = XaiClient()
        result = backend.fetch_xai_billing(client, {})
        self.assertEqual(client.calls, ['weekly', 'monthly', 'user', 'settings'])
        self.assertEqual(result['windows'][0]['usedPercent'], 15)
        self.assertEqual(result['extraUsage']['unit'], 'USD cents')
        self.assertNotIn('PRIVATE', json.dumps(result))
        http_client = backend.Client('https://example.test', 'management-example')
        with patch.object(http_client, '_request', return_value={}) as request:
            http_client.quota({'provider': 'xai', 'auth_index': 'idx'})
            http_client.quota_extra({'provider': 'xai', 'auth_index': 'idx'}, 'monthly')
            for call in request.call_args_list:
                self.assertEqual(call.kwargs['payload']['method'], 'GET')
                self.assertNotIn('chat/completions', call.kwargs['payload']['url'])

    def test_xai_unknown_weekly_usage_not_replaced_by_monthly_spending(self):
        class XaiClient:
            def quota(self, target):
                return envelope({'config': {'currentPeriod': {'type': 'USAGE_PERIOD_TYPE_WEEKLY',
                                                               'end': '2026-10-13T00:00:00Z'}}})
            def quota_extra(self, target, resource):
                return envelope({'config': {'monthlyLimit': 10000, 'used': 1000,
                                             'billingPeriodEnd': '2026-11-01T00:00:00Z'}})
        result = backend.fetch_xai_billing(XaiClient(), {})
        self.assertIsNone(result['windows'][0]['usedPercent'])
        self.assertEqual(result['windows'][0]['resetAt'], '2026-10-13T00:00:00+00:00')
        self.assertEqual(result['windows'][1]['usedPercent'], 10)

    def test_meta_consent_gates_network_twice_and_discards_minted_key(self):
        client = backend.Client('https://example.test', 'management-example')
        target = {'provider': 'meta', 'auth_index': 'idx', 'filename': 'synthetic-meta.json'}
        with patch.object(client, 'get') as get, patch.object(client, '_request') as request:
            with self.assertRaises(backend.SafeError):
                client.meta_quota(target)
            get.assert_not_called()
            request.assert_not_called()
        download = {'dca_token': 'dca:SYNTHETIC-ONLY', 'api_key': 'OLD-PRIVATE-KEY'}
        issued = {'api_key': 'MINTED-PRIVATE-KEY', 'user_email': 'private@example.com', 'subs_tier_name': 'pro',
                  'is_subs_active': True, 'subs_usage': {'window': {'used_percent': 25, 'window_duration_mins': 300,
                                                               'resets_at': 1791248400}, 'weekly': {'used_percent': 10}}}
        with server({'/v0/management/auth-files/download?name=synthetic-meta.json': (200, download, {}),
                     '/v0/management/api-call': (200, envelope(issued), {})}) as (url, calls):
            response = backend.Client(url, 'management-example').meta_quota(target, allow_key_issue=True)
            parsed = backend.quota_data(response, 'meta')
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][2]['method'], 'POST')
            self.assertEqual(calls[1][2]['url'], 'https://api.meta.ai/muse-code/key')
            self.assertEqual(calls[1][2]['header']['Authorization'], 'Bearer dca:SYNTHETIC-ONLY')
            self.assertEqual(parsed['windows'][0]['label'], '5 hours')
            self.assertTrue(parsed['subscriptionActive'])
            for secret in ['PRIVATE', 'SYNTHETIC', 'private@example']:
                self.assertNotIn(secret, json.dumps(parsed))

    def test_management_auth_flag_distinct_from_provider_status(self):
        with server({'/v0/management/api-call': (401, {'error': 'PRIVATE'}, {})}) as (url, _):
            with self.assertRaises(backend.SafeError) as error:
                backend.Client(url, 'management-example').quota({'provider': 'codex', 'auth_index': 'idx'})
            self.assertTrue(error.exception.auth_failed)
        with self.assertRaises(backend.SafeError) as error:
            backend.quota_body(envelope({'error': 'PRIVATE'}, 401))
        self.assertFalse(error.exception.auth_failed)
        rate = envelope({}, 429)
        rate['header'] = {'Retry-After': ['120'], 'PRIVATE-HEADER': ['SECRET']}
        with self.assertRaises(backend.SafeError) as error:
            backend.quota_body(rate)
        self.assertEqual(error.exception.retry_after, 120)


class BridgeSupplementTests(unittest.TestCase):
    def test_supplements_renewal_and_expiry_and_failure_isolation(self):
        class SupplementClient(FakeClient):
            fail = False
            def __init__(self, url, key):
                super().__init__({'/auth-files': {'files': [{'provider': 'codex', 'account_type': 'oauth',
                    'auth_index': 'idx', 'id_token': {'chatgpt_account_id': 'account-123'}}]}})
            def quota(self, target):
                return envelope({'rate_limit': {'primary_window': codex_window()}, 'credits': {'balance': '4.5'}})
            def quota_extra(self, target, resource):
                if self.fail:
                    raise backend.SafeError('Supplement unavailable.')
                if resource == 'subscription':
                    return envelope({'active_until': '2026-11-06T00:00:00Z', 'private': 'SECRET'})
                return envelope({'available_count': 1, 'credits': [{'reset_type': 'codex_rate_limits', 'status': 'available',
                                 'id': 'PRIVATE-ID', 'expires_at': '2026-10-20T00:00:00Z'}]})
        with tempfile.TemporaryDirectory() as root:
            bridge = backend.Bridge(backend.CredentialStore(root), SupplementClient)
            state = bridge.command({'op': 'connect', 'url': 'https://example.test', 'key': 'management-example'})
            account_id = state['accounts'][0]['id']
            result = bridge.command({'op': 'quota', 'id': account_id})
            self.assertEqual(result['renewalAt'], '2026-11-06T00:00:00+00:00')
            self.assertEqual(result['credits']['balance'], 4.5)
            self.assertEqual(result['credits']['resetCreditsAvailable'], 1)
            self.assertEqual(result['resetCredits'][0]['expiresAt'], '2026-10-20T00:00:00+00:00')
            self.assertNotIn('PRIVATE', json.dumps(result))
            SupplementClient.fail = True
            partial = bridge.command({'op': 'quota', 'id': account_id})
            self.assertEqual(len(partial['windows']), 1)
            self.assertEqual(len(partial['notices']), 2)
            self.assertNotIn('error', partial)

    def test_supplemental_rate_limits_preserve_maximum_backoff_with_good_windows(self):
        class LimitedClient(FakeClient):
            def __init__(self, url, key):
                super().__init__({'/auth-files': {'files': [{'provider': 'codex', 'account_type': 'oauth',
                    'auth_index': 'idx', 'id_token': {'chatgpt_account_id': 'account-123'}}]}})
            def quota(self, target):
                return envelope({'rate_limit': {'primary_window': codex_window()}})
            def quota_extra(self, target, resource):
                result = envelope({}, 429)
                result['header'] = {'Retry-After': ['180' if resource == 'subscription' else '60']}
                return result
        with tempfile.TemporaryDirectory() as root:
            bridge = backend.Bridge(backend.CredentialStore(root), LimitedClient)
            state = bridge.command({'op': 'connect', 'url': 'https://example.test', 'key': 'management-example'})
            result = bridge.command({'op': 'quota', 'id': state['accounts'][0]['id']})
            self.assertEqual(result['retryAfter'], 180)
            self.assertEqual(len(result['windows']), 1)
            self.assertNotIn('error', result)

        class PartialXai:
            def quota(self, target):
                result = envelope({}, 429)
                result['header'] = {'Retry-After': ['120']}
                return result
            def quota_extra(self, target, resource):
                return envelope({'config': {'monthlyLimit': 10000, 'used': 3000}})
        result = backend.fetch_xai_billing(PartialXai(), {})
        self.assertEqual(result['retryAfter'], 120)
        self.assertEqual(result['windows'][0]['usedPercent'], 30)

    def test_meta_bridge_requires_explicit_boolean_before_any_request(self):
        class MetaClient(FakeClient):
            count = 0
            def __init__(self, url, key):
                super().__init__({'/auth-files': {'files': [{'provider': 'meta', 'account_type': 'oauth',
                    'auth_index': 'idx', 'name': 'synthetic-meta.json', 'email': 'alex@example.com'}]}})
            def meta_quota(self, target, allow_key_issue=False):
                type(self).count += 1
                if allow_key_issue is not True:
                    raise AssertionError('Consent was not passed through.')
                return envelope({'subs_usage': {'weekly': {'used_percent': 5}}})
        with tempfile.TemporaryDirectory() as root:
            bridge = backend.Bridge(backend.CredentialStore(root), MetaClient)
            state = bridge.command({'op': 'connect', 'url': 'https://example.test', 'key': 'management-example'})
            row = state['accounts'][0]
            self.assertTrue(row['quotaConsentRequired'])
            self.assertTrue(row['quotaSupported'])
            self.assertNotIn('synthetic-meta', json.dumps(state))
            for flag in [None, False, 'true', 1]:
                result = bridge.command({'op': 'quota', 'id': row['id'], 'allowKeyIssue': flag})
                self.assertTrue(result['consentRequired'])
                self.assertEqual(MetaClient.count, 0)
            result = bridge.command({'op': 'quota', 'id': row['id'], 'allowKeyIssue': True})
            self.assertNotIn('error', result)
            self.assertEqual(MetaClient.count, 1)


if __name__ == '__main__':
    unittest.main()
