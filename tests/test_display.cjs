const test = require('node:test');
const assert = require('node:assert/strict');
const d = require('../Display.js');
const now = Date.parse('2026-10-06T12:00:00Z');
const accounts = [
  {id: 'a', label: 'first@example.com', provider: 'codex', plan: 'pro', requests: 0, failed: 0, status: 'active', lastActivityRank: 10},
  {id: 'b', label: 'second@example.com', provider: 'claude', requests: null, failed: null, status: 'unavailable', lastActivityRank: 30},
  {id: 'c', label: 'third@example.com', provider: 'codex', requests: 80, failed: 3, status: 'active', lastActivityRank: 20},
];
test('unknown values remain distinct from zero', () => {
  assert.equal(d.count(null), '—'); assert.equal(d.count(0), '0');
  assert.equal(d.compact(undefined), '—');
  assert.deepEqual(d.summary(accounts), {requests:80, failed:3, ready:2, partial:true});
  assert.equal(d.summary([accounts[1]]).requests, null);
});
test('filtering and sorting preserve input order and count zero above unknown', () => {
  assert.deepEqual(d.filtered(accounts, 'all', '', 'requests', false).map(a => a.id), ['c','a','b']);
  assert.deepEqual(d.filtered(accounts, 'all', '', 'recent', false).map(a => a.id), ['b','c','a']);
  assert.deepEqual(d.filtered(accounts, 'codex', ' FIRST ', 'recent', false).map(a => a.id), ['a']);
  assert.deepEqual(accounts.map(a => a.id), ['a','b','c']);
});
test('privacy search cannot reveal hidden identity', () => {
  assert.equal(d.filtered(accounts, 'all', 'first@example', 'recent', true).length, 0);
  assert.equal(d.filtered(accounts, 'all', 'codex', 'recent', true).length, 2);
});
test('activity respects newer approximate window over older exact timestamp', () => {
  assert.equal(d.activity({lastActivityKind:'window',lastActivityLabel:'23:50-00:00',lastRequestAt:'2026-10-05T00:00:00Z'},now), '23:50–00:00');
  assert.equal(d.activity({lastActivityKind:'exact',lastRequestAt:'2026-10-06T11:55:00Z'},now), '5m ago');
  assert.equal(d.activity({},now), 'Not reported');
});
test('reset handles elapsed and absent windows without suggesting fresh quota', () => {
  assert.equal(d.reset(null, now), 'Reset not reported');
  assert.equal(d.reset('2026-10-06T11:00:00Z', now), 'Reset due · refresh');
  assert.equal(d.reset('2026-10-06T14:14:00Z', now), 'Resets in 2h 14m');
});
test('provider filters include all accounts and recognizable provider labels', () => {
  assert.deepEqual(d.providers(accounts), [{value:'all',label:'All',count:3},{value:'claude',label:'Claude',count:1},{value:'codex',label:'Codex',count:2}]);
  assert.equal(d.providers([{provider:'xai'}])[1].label,'xAI');
});

test('quota cache survives same-account refresh but never a reused account slot', () => {
  const original = {id:'slot1', label:'first@example.com', provider:'codex', kind:'oauth', plan:'pro'};
  const quotas = {slot1:{windows:[{usedPercent:80}]}};
  assert.deepEqual(d.retainedQuotas(quotas,[original],[{...original,requests:99}]),quotas);
  for (const change of [{label:'other@example.com'},{provider:'claude'},{kind:'api_key'},{plan:'plus'},{id:'slot2'}])
    assert.deepEqual(d.retainedQuotas(quotas,[original],[{...original,...change}]),{});
  assert.deepEqual(d.retainedQuotas(quotas,[original],[]),{});
});
