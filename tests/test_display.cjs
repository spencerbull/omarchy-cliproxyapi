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
test('automatic queue covers supported subscriptions, gates Muse, and honors cache TTL', () => {
  const list = [
    {id:'codex',quotaSupported:true}, {id:'claude',quotaSupported:true},
    {id:'muse',quotaSupported:true,quotaConsentRequired:true}, {id:'unknown',quotaSupported:false},
  ];
  assert.deepEqual(d.dueQuotaIds(list,{}, {},now,false), ['codex','claude']);
  assert.deepEqual(d.dueQuotaIds(list,{}, {muse:true},now,false), ['codex','claude','muse']);
  const cache = {codex:{updatedAt:new Date(now-120000).toISOString()},claude:{lastAttemptAt:new Date(now-300000).toISOString(),error:'Unavailable'}};
  assert.deepEqual(d.dueQuotaIds(list,cache,{},now,false), ['claude']);
  assert.deepEqual(d.dueQuotaIds(list,cache,{},now,true), ['codex','claude']);
  cache.codex.retryAfter = 600;
  assert.deepEqual(d.dueQuotaIds(list,cache,{},now,true), ['claude']);
});
test('failed refresh retains last good limits and timestamp but delays retries', () => {
  const old = {windows:[{label:'Weekly',usedPercent:30}],updatedAt:'2026-10-06T11:00:00Z',plan:'pro'};
  const failure = {error:'Rate limited',updatedAt:'2026-10-06T12:00:00Z',windows:[],retryAfter:600};
  const actual = d.mergeQuota(old,failure);
  assert.deepEqual(actual.windows,old.windows);
  assert.equal(actual.updatedAt,old.updatedAt);
  assert.equal(actual.lastAttemptAt,failure.updatedAt);
  assert.equal(actual.error,'Rate limited');
  assert.equal(d.mergeQuota(actual,{windows:[],updatedAt:'2026-10-06T12:10:00Z'}).error,undefined);
});
test('full quota presentation includes credits, renewal, reset allowances, and extra spending units', () => {
  const quota = {windows:[{label:'Weekly',usedPercent:22}],renewalAt:'2026-11-01T12:00:00Z',
    credits:{balance:1234.5,resetCreditsAvailable:2,resetCreditsApplicable:1},
    resetCredits:[{expiresAt:'2026-10-10T12:00:00Z',applicable:true}],
    extraUsage:{enabled:true,usedCredits:350,monthlyLimit:2000,usedPercent:17.5,unit:'USD cents'}};
  const facts=d.quotaFacts(quota);
  assert.match(facts,/1.2k credits/); assert.match(facts,/1\/2 resets usable/); assert.match(facts,/Renews Nov 1/);
  assert.match(facts,/Extra \$3.50 \/ \$20.00/);
  assert.equal(d.quotaWindows(quota).length,2);
  assert.match(d.quotaDetails(quota),/1234.5/); assert.match(d.quotaDetails(quota),/1 applicable resets/);
  assert.match(d.quotaDetails(quota),/Reset 1 expires/);
  assert.equal(d.quotaFacts({credits:{balance:0,resetCreditsAvailable:0}}),'0 credits · 0 resets reported');
});
test('compact reset labels distinguish unknown and elapsed windows', () => {
  assert.equal(d.resetShort(null,now),'—');
  assert.equal(d.resetShort('2026-10-06T11:00:00Z',now),'Due');
  assert.equal(d.resetShort('2026-10-06T14:14:00Z',now),'2h14m');
  assert.equal(d.resetShort('2026-10-08T16:00:00Z',now),'2d4h');
});
test('explicit blocking overrides scoped activity without hiding reported Fable usage', () => {
  assert.equal(d.windowState({allowed:false,usedPercent:5}),'Blocked');
  assert.equal(d.windowState({limitReached:true,usedPercent:null}),'Blocked');
  for (const usedPercent of [0, 27, null])
    assert.equal(d.windowState({isActive:false,usedPercent}), '');
  assert.equal(d.windowState({isActive:false,allowed:false,usedPercent:5}), 'Blocked');
  assert.equal(d.windowState({allowed:true,usedPercent:5}),'');
});
