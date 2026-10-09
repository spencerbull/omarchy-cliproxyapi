"""Authenticated observer integration; all fixtures are synthetic."""
import copy
import json
import unittest
from test_backend import backend, FakeClient


def export():
    return {'schemaVersion': 1, 'source': 'omarchy-usage',
            'startedAt': '2026-10-06T00:00:00Z', 'partial': False, 'health': 'ok',
            'accounts': [{'authIndex': 'one', 'provider': 'codex', 'requests': 3, 'failed': 1,
                          'firstRequestAt': '2026-10-06T00:01:00Z', 'lastRequestAt': '2026-10-06T00:03:00Z',
                          'tokenMetrics': dict(total=120, input=100, output=20, cached=40, reasoning=5, cacheRead=40, cacheWrite=0),
                          'metricSamples': dict.fromkeys(backend.LEGACY_TOKEN_FIELDS, 2)}]}


def snapshot(payload=None, files=None):
    client = FakeClient({'/auth-files': {'files': files if files is not None else [
        {'auth_index': 'one', 'provider': 'codex', 'success': 500, 'failed': 10},
        {'auth_index': 'two', 'provider': 'codex', 'success': 900, 'failed': 5}]},
        backend.COLLECTOR_ROUTE: payload})
    return backend.snapshot(client, False), client


class CollectorTests(unittest.TestCase):
    def test_fixed_nondestructive_endpoint_and_separate_collection_period(self):
        result, client = snapshot(export())
        one, two = result['accounts']
        self.assertEqual(result['usageSource'], 'collector')
        self.assertTrue(result['tokenUsageAvailable'])
        self.assertEqual(one['requests'], 510)  # Limits activity retains server counters.
        self.assertEqual(one['usageRequests'], 3)
        self.assertEqual(one['usageFailed'], 1)
        self.assertEqual(two['usageRequests'], 0)
        self.assertEqual(one['tokenMetrics']['cacheRead'], {'value': 40, 'reported': 2, 'records': 3})
        self.assertEqual(one['tokenMetrics']['cacheWrite']['value'], 0)
        self.assertEqual(one['lastActivityKind'], 'exact')
        self.assertEqual(result['usageSince'], '2026-10-06T00:00:00+00:00')
        self.assertIn(backend.COLLECTOR_ROUTE, client.calls)
        self.assertFalse(any('queue' in route for route in client.calls))
        self.assertNotIn('authIndex', json.dumps(result))

    def test_ambiguous_removed_and_wrong_provider_are_never_joined(self):
        for files in [[{'auth_index': 'one', 'provider': 'codex'}] * 2,
                      [{'auth_index': 'two', 'provider': 'codex'}],
                      [{'auth_index': 'one', 'provider': 'claude'}]]:
            result, _ = snapshot(export(), files)
            self.assertEqual(result['usageUnattributedRecords'], 3)
            self.assertTrue(all(a['tokenMetrics']['input']['value'] is None for a in result['accounts']))

    def test_partial_health_propagates_and_payload_text_is_discarded(self):
        payload = export()
        payload.update(health='degraded', notice='SECRET-ERROR', credentials='PRIVATE-KEY')
        payload['accounts'][0].update(email='PRIVATE-EMAIL', apiKey='PRIVATE-KEY')
        result, _ = snapshot(payload)
        self.assertTrue(result['usagePartial'])
        self.assertTrue(result['accounts'][0]['usagePartial'])
        for forbidden in ['SECRET-', 'PRIVATE-']:
            self.assertNotIn(forbidden, json.dumps(result))

    def test_absent_collector_falls_back_without_fake_zeros(self):
        result, _ = snapshot()
        self.assertEqual(result['usageSource'], 'unavailable')
        self.assertFalse(result['tokenUsageAvailable'])
        self.assertNotIn('usageRequests', result['accounts'][0])
        self.assertIsNone(result['accounts'][0]['tokenMetrics']['total']['value'])

    def test_malformed_export_rejected_atomically(self):
        changes = [lambda p: p.update(schemaVersion=999),
                   lambda p: p['accounts'].append(copy.deepcopy(p['accounts'][0])),
                   lambda p: p['accounts'][0].update(requests=-1),
                   lambda p: p['accounts'][0].update(failed=4),
                   lambda p: p['accounts'][0].update(firstRequestAt='invalid'),
                   lambda p: p['accounts'][0]['metricSamples'].update(input=4),
                   lambda p: p['accounts'][0]['tokenMetrics'].update(input=float('nan')),
                   lambda p: p['accounts'][0]['tokenMetrics'].update(input=None),
                   lambda p: p['accounts'][0]['tokenMetrics'].update(total=True)]
        for change in changes:
            with self.subTest(change=change):
                payload = export(); change(payload)
                result, _ = snapshot(payload)
                self.assertEqual(result['usageSource'], 'unavailable')
                self.assertIn('collectorNotice', result)
                self.assertNotIn('usageRequests', result['accounts'][0])

    def test_failure_does_not_hide_limits_but_authentication_failure_stops(self):
        class Client(FakeClient):
            auth_failed = False
            def get(self, endpoint, optional=False):
                if endpoint == backend.COLLECTOR_ROUTE:
                    raise backend.SafeError('PRIVATE-ERROR', auth_failed=self.auth_failed)
                return {'files': []} if endpoint == '/auth-files' else None
        client = Client({})
        result = backend.snapshot(client, False)
        self.assertIn('collectorNotice', result)
        self.assertNotIn('PRIVATE', json.dumps(result))
        client.auth_failed = True
        with self.assertRaises(backend.SafeError):
            backend.snapshot(client, False)

    def test_synthetic_collector_export_matches_adapter_contract(self):
        from demo_server import collector_fixture
        result, _ = snapshot(collector_fixture(), [
            {'auth_index': f'{i + 1:016x}', 'provider': family}
            for i, family in enumerate(['codex', 'claude', 'codex', 'claude'])])
        self.assertEqual(result['usageSource'], 'collector')
        self.assertEqual(result['usageUnattributedRecords'], 0)
        self.assertTrue(result['usageHistory']['available'])
        self.assertTrue(result['usageHistory']['buckets'])
        self.assertGreater(sum(a['usageRequests'] for a in result['accounts']), 0)
        self.assertGreater(sum(a['tokenMetrics']['total']['value'] or 0 for a in result['accounts']), 0)
