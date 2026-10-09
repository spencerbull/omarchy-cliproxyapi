"""Dated collector export validation; no live services or private state."""
import copy
import datetime
import json
import unittest
from test_collector import export, snapshot
from test_backend import backend

INDEX = '0123456789abcdef'


def history_export():
    payload = export()
    today = datetime.datetime.now(datetime.timezone.utc)
    since = today - datetime.timedelta(days=35)
    payload.update(schemaVersion=2, startedAt=since.isoformat(), historySince=since.isoformat(),
                   asOf=today.isoformat(), historyPartial=False, historyDropped=0)
    account = payload['accounts'][0]
    account['authIndex'] = INDEX
    payload['buckets'] = [dict(date=today.date().isoformat(), provider='codex', authIndex=INDEX,
                               model='gpt-test', requests=3, tokenMetrics=account['tokenMetrics'].copy(),
                               metricSamples=account['metricSamples'].copy())]
    return payload


def history_snapshot(payload, files=None):
    return snapshot(payload, files if files is not None else [{'auth_index': INDEX, 'provider': 'codex'}])[0]


class CollectorHistoryTests(unittest.TestCase):
    def test_model_metrics_attribution_and_no_private_index(self):
        result = history_snapshot(history_export())
        history = result['usageHistory']
        self.assertTrue(history['available'])
        self.assertFalse(history['partial'])
        row = history['buckets'][0]
        self.assertEqual(row['accountId'], result['accounts'][0]['id'])
        self.assertEqual(row['model'], 'gpt-test')
        self.assertEqual(row['usageRecords'], 3)
        self.assertEqual(row['tokenMetrics']['input'], {'value': 100, 'reported': 2, 'records': 3})
        self.assertNotIn(INDEX, json.dumps(result))
        self.assertNotIn('authIndex', json.dumps(result))

    def test_removed_and_ambiguous_accounts_stay_in_global_history(self):
        for files in ([], [{'auth_index': INDEX, 'provider': 'claude'}],
                      [{'auth_index': INDEX, 'provider': 'codex'}]*2):
            result = history_snapshot(history_export(), files)
            self.assertTrue(result['usageHistory']['available'])
            self.assertIsNone(result['usageHistory']['buckets'][0]['accountId'])
            self.assertEqual(result['usageHistory']['buckets'][0]['tokenMetrics']['total']['value'], 120)

    def test_boundary_retention_and_distinct_models_accounts_providers(self):
        payload = history_export()
        row = payload['buckets'][0]
        today = datetime.date.fromisoformat(row['date'])
        for changes in ({'date': (today-datetime.timedelta(days=29)).isoformat()},
                        {'model': 'other-model'}, {'provider': 'claude'}, {'authIndex': 'fedcba9876543210'}):
            payload['buckets'].append(dict(copy.deepcopy(row), **changes))
        self.assertEqual(len(history_snapshot(payload)['usageHistory']['buckets']), 5)
        payload['buckets'][1]['date'] = (today-datetime.timedelta(days=30)).isoformat()
        self.assertFalse(history_snapshot(payload)['usageHistory']['available'])

    def test_unknown_is_not_zero_and_partial_propagates(self):
        payload = history_export()
        row = payload['buckets'][0]
        row.update(model='unknown', tokenMetrics=dict.fromkeys(backend.LEGACY_TOKEN_FIELDS),
                   metricSamples=dict.fromkeys(backend.LEGACY_TOKEN_FIELDS, 0))
        payload['historyPartial'] = True
        result = history_snapshot(payload)['usageHistory']
        self.assertTrue(result['partial'])
        self.assertEqual(result['buckets'][0]['tokenMetrics']['total'], {'value': None, 'reported': 0, 'records': 3})
        payload['historyPartial'] = False
        payload['historyDropped'] = 1
        self.assertTrue(history_snapshot(payload)['usageHistory']['partial'])

    def test_bad_history_never_discards_lifetime(self):
        changes = [lambda p:p.update(buckets=None), lambda p:p.update(historySince='invalid'),
                   lambda p:p.update(asOf='9999-01-01T00:00:00Z'), lambda p:p.update(historyPartial=0),
                   lambda p:p.update(historyDropped=-1), lambda p:p.update(buckets=p['buckets']*2049),
                   lambda p:p['buckets'].append(copy.deepcopy(p['buckets'][0])),
                   lambda p:p['buckets'][0].update(date=None), lambda p:p['buckets'][0].update(date='2026-99-99'),
                   lambda p:p['buckets'][0].update(model='private@example.com'),
                   lambda p:p['buckets'][0].update(model='management-secret-example'),
                   lambda p:p['buckets'][0].update(authIndex='private-auth'),
                   lambda p:p['buckets'][0].update(provider='private/provider'),
                   lambda p:p['buckets'][0].update(requests=True),
                   lambda p:p['buckets'][0]['metricSamples'].update(input=4),
                   lambda p:p['buckets'][0]['tokenMetrics'].update(input=float('inf')),
                   lambda p:p['buckets'][0]['tokenMetrics'].update(input=None)]
        for change in changes:
            payload=history_export();change(payload)
            with self.subTest(change=change):
                result=history_snapshot(payload)
                self.assertFalse(result['usageHistory']['available'])
                self.assertEqual(result['usageSource'], 'collector')
                self.assertEqual(result['accounts'][0]['tokenMetrics']['total']['value'], 120)

    def test_global_counter_overflow_is_rejected(self):
        payload=history_export()
        payload['buckets'][0]['tokenMetrics']['total']=2**53-1
        payload['buckets'].append(dict(copy.deepcopy(payload['buckets'][0]), model='other'))
        self.assertFalse(history_snapshot(payload)['usageHistory']['available'])

    def test_schema1_and_absent_collector_explain_history_unavailable(self):
        for payload in (export(), None, [], True):
            result,_ = snapshot(payload)
            self.assertFalse(result['usageHistory']['available'])
            self.assertTrue(result['usageHistory']['reason'])
