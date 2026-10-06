import json
import unittest
from unittest.mock import patch
from test_backend import backend
from test_subscription_quotas import envelope


class XaiSubscriptionTests(unittest.TestCase):
    def test_known_plan_precedence_and_unknown_redaction(self):
        self.assertEqual(backend.xai_subscription_plan({'subscriptionTier': 'SuperGrokPro'},
            {'subscription_tier_display': 'SuperGrok Heavy'}), 'SuperGrok Heavy')
        self.assertEqual(backend.xai_subscription_plan({'subscription_tier': 'XPremiumPlus'}, {}), 'X Premium+')
        self.assertIsNone(backend.xai_subscription_plan({'subscriptionTier': 'PRIVATE-USER-TOKEN'},
            {'subscription_tier_display': 'PRIVATE-LABEL'}))

    def test_user_and_settings_fixed_get_routes(self):
        client = backend.Client('https://example.test', 'management-example')
        with patch.object(client, '_request', return_value={}) as request:
            for resource in ('user', 'settings'):
                client.quota_extra({'provider': 'xai', 'auth_index': 'idx'}, resource)
            payloads = [call.kwargs['payload'] for call in request.call_args_list]
            self.assertEqual([row['url'] for row in payloads], [
                'https://cli-chat-proxy.grok.com/v1/user?include=subscription',
                'https://cli-chat-proxy.grok.com/v1/settings'])
            for payload in payloads:
                self.assertEqual(payload['method'], 'GET')
                self.assertEqual(payload['header']['Authorization'], 'Bearer $TOKEN$')
                self.assertNotIn('data', payload)

    def test_known_product_enum_has_meaningful_static_label(self):
        payload = {'config': {'productUsage': [{'product': 'GrokBuild', 'usagePercent': 25},
                                               {'product': 'PRIVATE-KEY', 'usagePercent': 12}]}}
        result = backend.quota_data(envelope(payload), 'xai')
        self.assertEqual(result['windows'][0]['label'], 'Grok Build')
        self.assertEqual(result['windows'][1]['label'], 'Product 2')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_profile_reads_preserve_billing_on_errors_and_backoff(self):
        class Client:
            fail = False
            def quota(self, target):
                return envelope({'config': {'currentPeriod': {'type': 'USAGE_PERIOD_TYPE_WEEKLY'},
                                             'creditUsagePercent': 12}})
            def quota_extra(self, target, resource):
                if resource == 'monthly':
                    return envelope({'config': {'monthlyLimit': 1000, 'used': 0}})
                if self.fail:
                    result = envelope({'error': 'PRIVATE'}, 429)
                    result['header'] = {'Retry-After': ['240' if resource == 'user' else '60']}
                    return result
                return envelope({'subscriptionTier': 'SuperGrokPro'} if resource == 'user' else
                                {'subscription_tier_display': 'SuperGrok Heavy'})
        client = Client()
        result = backend.fetch_xai_billing(client, {})
        self.assertEqual(result['plan'], 'SuperGrok Heavy')
        self.assertEqual(len(result['windows']), 2)
        client.fail = True
        result = backend.fetch_xai_billing(client, {})
        self.assertEqual(len(result['windows']), 2)
        self.assertIsNone(result['plan'])
        self.assertEqual(result['retryAfter'], 240)
        self.assertNotIn('PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
