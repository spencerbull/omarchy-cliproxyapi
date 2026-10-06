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

test('usage aggregates accounts once by provider and preserves partial token coverage', () => {
  const metric = (value, reported=1, records=1) => ({value, reported, records});
  const rows = [
    {provider:'codex',requests:4,failed:0,usageRecords:2,tokenMetrics:{input:metric(120,2,2),cached:metric(80,1,2)},metricsLabel:'Upstream attempts'},
    {provider:'codex',requests:6,failed:1,usageRecords:1,tokenMetrics:{input:metric(30),cached:metric(null,0)},metricsLabel:'Upstream attempts'},
    {provider:'claude',requests:null,failed:null,usageRecords:0,tokenMetrics:{},metricsLabel:'Recorded requests'}
  ];
  const groups=d.usageGroups(rows), codex=groups.find(g=>g.provider==='codex');
  assert.equal(codex.requests.value,10); assert.equal(codex.requests.partial,false);
  assert.deepEqual(codex.tokenMetrics.input,{value:150,reported:3,records:3,partial:false});
  assert.deepEqual(codex.tokenMetrics.cached,{value:80,reported:1,records:3,partial:true});
  assert.equal(codex.tokenMetrics.total.value,null); // Never sum overlapping fields.
  assert.equal(d.usageSummary(rows).requests.partial,true);
  assert.equal(d.usageSummary(rows).tokenMetrics.input.partial,true);
  assert.equal(d.usageSummary(rows).metricsLabel,'Mixed request counters');
  assert.equal(d.usageSummary(rows).usageRecords,3);
  assert.equal(d.metricText(codex.tokenMetrics.cached),'80*');
});
test('usage chart preserves zero and unknown without invalid widths', () => {
  const groups=d.usageGroups([{provider:'codex',requests:0,tokenMetrics:{}},{provider:'claude',requests:null,tokenMetrics:{}}]);
  assert.equal(d.usageMaximum(groups,'requests'),0);
  assert.equal(d.metricRatio({value:0},0),0);
  assert.equal(d.metricRatio({value:null},100),0);
  assert.equal(d.metricRatio({value:25},100),0.25);
  assert.equal(d.metricText(d.usageMetric(groups[0],'requests')),'—');
  assert.equal(d.metricText(d.usageMetric(groups[1],'requests')),'0');
  assert.equal(d.usageSummary([]).tokenMetrics.input.value,null);
  assert.equal(d.usageSummary([]).requests.value,null);
});
test('usage aggregation reflects filtered accounts and latest exact or approximate activity', () => {
  const rows=[{id:'a',provider:'codex',label:'work@example.com',requests:12,lastActivityRank:10,lastActivityKind:'exact',lastRequestAt:'2026-10-06T11:55:00Z'},
    {id:'b',provider:'codex',label:'home@example.com',requests:8,lastActivityRank:20,lastActivityKind:'window',lastActivityLabel:'12:00-12:10'}];
  assert.equal(d.usageSummary(rows).lastActivityKind,'window');
  assert.equal(d.usageSummary(d.filtered(rows,'all','work','provider',false)).requests.value,12);
  assert.equal(d.usageSummary(d.filtered(rows,'all','work','provider',true)).requests.value,null);
});
test('collector aggregation uses one persistent period and marks degraded totals partial', () => {
  const rows=[{provider:'codex',requests:900,failed:30,usageSource:'collector',usageRequests:5,usageFailed:1,usagePartial:true,
    tokenMetrics:{total:{value:120,reported:2,records:5}}},
    {provider:'codex',requests:200,failed:0,usageSource:'collector',usageRequests:0,usageFailed:0,tokenMetrics:{}}];
  const summary=d.usageSummary(rows);
  assert.equal(summary.requests.value,5);
  assert.equal(summary.failed.value,1);
  assert.equal(summary.requests.partial,true);
  assert.equal(summary.metricsLabel,'Collected attempts');
  assert.equal(summary.tokenMetrics.total.value,120);
  assert.equal(summary.tokenMetrics.total.partial,true);
});
test('unused attributable collector accounts do not make complete token totals partial', () => {
  const empty={provider:'codex',usageSource:'collector',usageRequests:0,usageRecords:0,tokenMetrics:{}};
  const active={provider:'codex',usageSource:'collector',usageRequests:2,usageRecords:2,
    tokenMetrics:{input:{value:100,reported:2,records:2}}};
  assert.deepEqual(d.usageSummary([active,empty]).tokenMetrics.input,{value:100,reported:2,records:2,partial:false});
  assert.equal(d.usageSummary([empty]).tokenMetrics.input.value,null);
  assert.equal(d.usageSummary([active,{...empty,usageRequests:null}]).tokenMetrics.input.partial,true);
});
