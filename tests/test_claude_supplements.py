import json
import tempfile
import unittest
from unittest.mock import patch
from test_backend import backend, FakeClient
from test_subscription_quotas import envelope


NOW = backend.datetime.datetime(2026, 10, 6, tzinfo=backend.datetime.timezone.utc).timestamp()


def grant(identifier='synthetic-grant', left=2, **fields):
    return {'id': identifier, 'resets_total': 4, 'resets_left': left, 'usable_now': True,
            'ends_at': '2026-10-20T00:00:00Z', **fields}


def grants_payload(rows, **fields):
    return {'cedar_ember': {'eligible': True, 'at_limit': True, 'grants': rows, **fields}}


class ClaudeSupplementTests(unittest.TestCase):
    def test_read_routes_only_and_claude_headers(self):
        client = backend.Client('https://example.test', 'management-example')
        target = {'provider': 'claude', 'auth_index': 'idx'}
        with patch.object(client, '_request', return_value={}) as request:
            client.quota_extra(target, 'profile')
            client.quota_extra(target, 'reset-grants')
            payloads = [call.kwargs['payload'] for call in request.call_args_list]
            self.assertEqual([row['url'] for row in payloads], [
                'https://api.anthropic.com/api/oauth/profile',
                'https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1'])
            for payload in payloads:
                self.assertEqual(payload['method'], 'GET')
                self.assertEqual(payload['header']['Authorization'], 'Bearer $TOKEN$')
                self.assertEqual(payload['header']['anthropic-beta'], 'oauth-2025-04-20')
                self.assertNotIn('data', payload)
            for forbidden in ('claim', 'reset', 'reset_rate_limits'):
                with self.assertRaises(backend.SafeError):
                    client.quota_extra(target, forbidden)
            self.assertEqual(request.call_count, 2)

    def test_reset_totals_distinct_from_current_applicability(self):
        rows = [grant('active', 2), grant('expired', 1, ends_at='2026-10-05T00:00:00Z'),
                grant('future', 1, starts_at='2026-10-07T00:00:00Z'),
                grant('paused', 1, paused=True), grant('not-usable', 1, usable_now=False)]
        result = backend.parse_claude_reset_grants(grants_payload(rows), now=NOW)
        self.assertEqual(result['available'], 6)
        self.assertEqual(result['applicable'], 2)
        self.assertEqual(len(result['entries']), 5)
        self.assertFalse(result['entries'][0]['applicable'])
        self.assertNotIn('id', json.dumps(result))
        self.assertNotIn('active', json.dumps(result))
        for block_fields in [{'eligible': False}, {'at_limit': False}, {'cooldown_until': '2026-10-06T00:01:00Z'}]:
            result = backend.parse_claude_reset_grants(grants_payload([grant()], **block_fields), now=NOW)
            self.assertEqual(result['available'], 2)
            self.assertEqual(result['applicable'], 0)
        result = backend.parse_claude_reset_grants(grants_payload([grant(use_requires_limit=False)], at_limit=False), now=NOW)
        self.assertEqual(result['applicable'], 2)

    def test_malformed_or_duplicate_grants_reject_entire_status(self):
        invalids = [{}, {'cedar_ember': {}}, grants_payload([grant(), grant()]),
                    grants_payload([grant(left=5)]), grants_payload([grant(left=True)]),
                    grants_payload([grant(usable_now='yes')]), grants_payload([grant(ends_at='bad')]),
                    grants_payload([grant(), {'id': 'missing-fields'}])]
        for value in invalids:
            with self.subTest(value=value), self.assertRaises(backend.SafeError):
                backend.parse_claude_reset_grants(value, now=NOW)
        self.assertEqual(backend.parse_claude_reset_grants(grants_payload([]), now=NOW)['available'], 0)
        result = backend.parse_claude_reset_grants(grants_payload([grant(ends_at=None)]), now=NOW)
        self.assertEqual(result['available'], 2)
        self.assertEqual(result['entries'], [])

    def test_profile_plan_never_echoes_identity(self):
        for account, expected in [({'has_claude_max': True}, 'max'), ({'has_claude_pro': True}, 'pro'),
                                  ({'has_claude_max': False, 'has_claude_pro': False}, 'free'), ({}, None)]:
            profile = {'account': {**account, 'email': 'private@example.com', 'uuid': 'PRIVATE-ID'}}
            self.assertEqual(backend.claude_profile_plan(profile), expected)
        self.assertEqual(backend.claude_profile_plan({'account': {'has_claude_max': True},
            'organization': {'organization_type': 'claude_team', 'subscription_status': 'active'}}), 'team')

    def test_complete_and_failed_optional_reads_preserve_base_usage_and_backoff(self):
        class ClaudeClient(FakeClient):
            mode = 'success'
            def __init__(self, url, key):
                super().__init__({'/auth-files': {'files': [{'provider': 'claude', 'account_type': 'oauth',
                                                          'auth_index': 'idx'}]}})
            def quota(self, target):
                return envelope({'five_hour': {'utilization': 17}, 'seven_day': {'utilization': 9}})
            def quota_extra(self, target, resource):
                if self.mode == 'missing':
                    return envelope({}, 404)
                if self.mode == 'limited':
                    result = envelope({}, 429)
                    result['header'] = {'Retry-After': ['180' if resource == 'profile' else '60']}
                    return result
                if resource == 'profile':
                    return envelope({'account': {'has_claude_max': True, 'uuid': 'PRIVATE-ID'}})
                return envelope(grants_payload([grant(ends_at='2099-10-20T00:00:00Z')]))
        with tempfile.TemporaryDirectory() as root:
            bridge = backend.Bridge(backend.CredentialStore(root), ClaudeClient)
            state = bridge.command({'op': 'connect', 'url': 'https://example.test', 'key': 'management-example'})
            account_id = state['accounts'][0]['id']
            result = bridge.command({'op': 'quota', 'id': account_id})
            self.assertEqual(result['plan'], 'max')
            self.assertEqual(result['credits']['resetCreditsAvailable'], 2)
            self.assertEqual(result['credits']['resetCreditsApplicable'], 2)
            self.assertEqual(result['resetCredits'], [{'expiresAt': '2099-10-20T00:00:00+00:00', 'applicable': True}])
            self.assertNotIn('PRIVATE', json.dumps(result))
            for mode in ('missing', 'limited'):
                ClaudeClient.mode = mode
                result = bridge.command({'op': 'quota', 'id': account_id})
                self.assertEqual(len(result['windows']), 2)
                self.assertNotIn('error', result)
                self.assertIsNone(result['credits'])
                self.assertEqual(len(result['notices']), 2)
                if mode == 'limited':
                    self.assertEqual(result['retryAfter'], 180)


if __name__ == '__main__':
    unittest.main()
