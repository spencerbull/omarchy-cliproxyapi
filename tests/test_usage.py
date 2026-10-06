import json
import threading
import unittest
import urllib.request

from demo_server import create_server
from test_accounts import snapshot
from test_backend import backend


def usage(rows, **model_totals):
    return {'apis': {'PRIVATE-CLIENT-KEY': {'models': {
        'model': dict(model_totals, details=rows)}}}}


def record(index='one', tokens=None, stamp='2026-10-06T00:00:00Z'):
    return {'auth_index': index, 'tokens': tokens, 'timestamp': stamp, 'failed': False}


class UsageTests(unittest.TestCase):
    def test_partial_metrics_preserve_zero_and_existing_total_behavior(self):
        rows = [record(tokens={'total_tokens': 120, 'input_tokens': 100, 'output_tokens': 20,
                               'cached_tokens': 0, 'reasoning_tokens': 5}),
                record(tokens={'input_tokens': 200, 'output_tokens': 40}), record()]
        result, _ = snapshot([{'auth_index': 'one', 'provider': 'codex'}], usage(rows))
        account = result['accounts'][0]
        metrics = account['tokenMetrics']
        self.assertEqual(metrics['total'], {'value': 120, 'reported': 1, 'records': 3})
        self.assertEqual(metrics['input'], {'value': 300, 'reported': 2, 'records': 3})
        self.assertEqual(metrics['output']['value'], 60)
        self.assertEqual(metrics['cached'], {'value': 0, 'reported': 1, 'records': 3})
        self.assertEqual(metrics['reasoning']['value'], 5)
        for name in ('cacheRead', 'cacheWrite'):
            self.assertEqual(metrics[name], {'value': None, 'reported': 0, 'records': 3})
        self.assertEqual(account['usageRecords'], 3)
        self.assertIsNone(account['tokens'])
        self.assertIsNone(account['models'][0]['tokens'])

    def test_invalid_counts_and_aliases_do_not_become_reported_values(self):
        invalid = [None, True, False, -1, '12', 1.5, float('nan'), float('inf'), 2**53, {}, []]
        rows = [record(tokens={'input_tokens': value}) for value in invalid]
        rows += [record(tokens={'inputTokens': 99, 'totalTokens': 100, 'cache_read_tokens': 5,
                                'cache_write_tokens': 7, 'cached_tokens': 3}),
                 record(tokens={'input_tokens': 12.0})]
        metrics = backend.legacy_token_metrics([('model', row) for row in rows])
        self.assertEqual(metrics['input'], {'value': 12, 'reported': 1, 'records': len(rows)})
        self.assertIsNone(metrics['total']['value'])
        self.assertEqual(metrics['cached']['value'], 3)
        self.assertIsNone(metrics['cacheRead']['value'])
        self.assertIsNone(metrics['cacheWrite']['value'])

    def test_only_unique_exact_auth_index_attribution(self):
        files = [{'auth_index': index, 'provider': 'codex'} for index in ('one', 'two', 'dup', 'dup')]
        rows = [record(index, {'total_tokens': 10}) for index in ('one', 'two', 'dup', 'unknown', None)]
        rows += [{'authIndex': 'one', 'tokens': {'total_tokens': 90}}, None, 'invalid']
        result, _ = snapshot(files, usage(rows, total_requests=900, total_tokens=999999))
        self.assertEqual([a['usageRecords'] for a in result['accounts']], [1, 1, 0, 0])
        self.assertEqual(result['usageUnattributedRecords'], 4)
        self.assertEqual(result['accounts'][0]['tokenMetrics']['total']['value'], 10)
        self.assertNotIn('PRIVATE-CLIENT-KEY', json.dumps(result))
        self.assertNotIn('auth_index', json.dumps(result))

    def test_aggregates_do_not_fabricate_records(self):
        result, _ = snapshot([{'auth_index': 'one'}], usage([], total_tokens=900, total_requests=4))
        account = result['accounts'][0]
        self.assertEqual(account['usageRecords'], 0)
        self.assertEqual(result['usageUnattributedRecords'], 0)
        self.assertTrue(all(metric == {'value': None, 'reported': 0, 'records': 0}
                            for metric in account['tokenMetrics'].values()))

    def test_timestamp_bounds_are_normalized_and_scoped(self):
        rows = [record(stamp='2026-10-06T02:30:00+02:00'), record(stamp='2026-10-05T23:45:00Z'),
                record(stamp='not-a-date'), record(stamp='2026-10-07T00:00:00'),
                record('unknown', stamp='2026-10-08T00:00:00Z')]
        result, _ = snapshot([{'auth_index': 'one'}], usage(rows))
        account = result['accounts'][0]
        self.assertEqual(account['usageFirstAt'], '2026-10-05T23:45:00+00:00')
        self.assertEqual(account['usageLastAt'], '2026-10-06T00:30:00+00:00')
        self.assertEqual(account['usageRecords'], 4)

    def test_missing_usage_and_opaque_api_keys_remain_unknown(self):
        result, _ = snapshot([{'auth_index': 'one'}], keys={'codex': {'PRIVATE-KEY': {'success': 3}}})
        self.assertFalse(result['usageAvailable'])
        for account in result['accounts']:
            self.assertEqual(account['usageRecords'], 0)
            self.assertIsNone(account['usageFirstAt'])
            self.assertIsNone(account['usageLastAt'])
            self.assertTrue(all(metric['value'] is None for metric in account['tokenMetrics'].values()))

    def test_deduplicated_api_key_details_are_not_assigned_to_opaque_row(self):
        result, _ = snapshot([{'auth_index': 'one', 'account_type': 'api_key', 'account': 'PRIVATE-KEY'}],
                             usage([record(tokens={'total_tokens': 25})]),
                             keys={'codex': {'endpoint|PRIVATE-KEY': {'success': 3}}})
        self.assertEqual(len(result['accounts']), 1)
        self.assertEqual(result['accounts'][0]['usageRecords'], 0)
        self.assertEqual(result['usageUnattributedRecords'], 1)

    def test_demo_http_legacy_and_modern_toggle(self):
        service = create_server()
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:' + str(service.server_port)
            client = backend.Client(base, 'demo-only')
            result = backend.snapshot(client, False)
            self.assertTrue(result['usageAvailable'])
            self.assertEqual(result['usageUnattributedRecords'], 1)
            self.assertEqual(result['accounts'][1]['tokenMetrics']['total']['reported'], 2)
            self.assertIsNone(result['accounts'][3]['tokenMetrics']['input']['value'])
            self.assertNotIn('synthetic-client-key', json.dumps(result))
            request = urllib.request.Request(base + '/demo/usage/disable', data=b'{}',
                                             headers={'Authorization': 'Bearer demo-only'}, method='POST')
            with urllib.request.urlopen(request) as response:
                self.assertFalse(json.load(response)['usage_enabled'])
            result = backend.snapshot(client, False)
            self.assertFalse(result['usageAvailable'])
            self.assertTrue(all(account['usageRecords'] == 0 for account in result['accounts']))
            self.assertEqual((service.quota_calls, service.downloads), (0, 0))
        finally:
            service.shutdown()
            service.server_close()
            thread.join()
