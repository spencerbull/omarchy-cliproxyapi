# Omarchy usage collector for CLIProxyAPI

This optional **server-side** companion supplies token history to the Omarchy
plugin on CLIProxyAPI v8. It observes the host's `usage.handle` events and exposes
one authenticated, non-consuming summary endpoint. It never reads `/usage-queue`
and can coexist with another usage observer.

Integration target: **CLIProxyAPI v8.0.16**, C ABI 1, RPC schema 6. The small local
wire structs mirror that release's `sdk/pluginapi/types.go`,
`internal/pluginhost/rpc_schema.go`, and Go usage/management examples. This is a
C shared library, not Go's `plugin` build mode; it needs no server module import
or local `replace`. Upstream-derived ABI declarations are MIT licensed; see
[UPSTREAM_LICENSE](UPSTREAM_LICENSE).

## Build and test

Build on the server's Linux architecture/libc with Go 1.25+ and a C compiler:

```sh
cd collector
go test -race ./...
go build -buildmode=c-shared -o omarchy-usage.so .
python3 test_abi.py ./omarchy-usage.so
```

The Python test uses only synthetic data in a temporary private directory and
loads the actual shared library through its C ABI. No credentials or running
server are needed. The generated `.h` is not required at runtime. Do not commit
binaries or collector state.

## Server configuration (operator action)

Preparing this companion does **not** install or enable it on a running server.
Use your existing deployment's change/restart procedure. Back up its config,
retain all existing plugin settings, and:

1. Place `omarchy-usage.so` in the configured `plugins.dir`. The basename must be
   `omarchy-usage.so` so its plugin ID matches the configuration below.
2. Create `/var/lib/cliproxyapi/omarchy-usage` owned by the server service user,
   with mode `0700`. Existing state and lock files must be mode `0600`,
   owned by the effective service UID, regular files with only one hard link. For containers, mount a persistent private volume at that
   path with matching UID ownership. Use an absolute path with no symlink
   components; pre-existing group/world-accessible directories are rejected.
3. Merge these entries into the existing server configuration:

```yaml
plugins:
  enabled: true
  dir: plugins # Keep the deployment's existing directory.
  configs:
    omarchy-usage:
      enabled: true
      data_dir: /var/lib/cliproxyapi/omarchy-usage
```

4. Restart the server through its existing service/container manager. Do not
   change management authentication or expose management to the public internet.
   The companion reuses the server's management authentication; it needs no
   additional secret, port, network client, or queue reader.
5. Refresh the desktop Usage pane. Once normal requests pass through the server,
   their observed token counters appear. Historical events from before enablement
   are not backfilled. The collector sends no inference requests for verification.

The only route is:

```text
GET /v0/management/plugins/omarchy-usage/summary
```

It is registered as a Management API route with **no Menu or Resources entries**,
so the upstream management authentication middleware applies. It does not create
an unauthenticated `/v0/resource` page. Reads are idempotent and `Cache-Control:
no-store`; there are no reset, import, or mutation HTTP routes. Use the existing
management client/key handling rather than placing keys in shell command lines.

## Data contract and interpretation

```json
{
  "schemaVersion": 2,
  "historySince": "2026-01-01T00:00:00Z",
  "asOf": "2026-01-01T01:00:00Z",
  "historyPartial": false,
  "historyDropped": 0,
  "buckets": [],
  "source": "omarchy-usage",
  "startedAt": "2026-01-01T00:00:00Z",
  "updatedAt": "2026-01-01T01:00:00Z",
  "coverage": "Observed upstream attempts since collector start",
  "health": "ok",
  "partial": false,
  "dropped": 0,
  "notice": "Accounting caveats are included here.",
  "accounts": [{
    "authIndex": "0123456789abcdef",
    "provider": "codex",
    "requests": 2,
    "failed": 1,
    "firstRequestAt": "2026-01-01T00:10:00Z",
    "lastRequestAt": "2026-01-01T01:00:00Z",
    "tokenMetrics": {
      "total": 120, "input": 100, "output": 20,
      "cached": 25, "reasoning": 5, "cacheRead": 0, "cacheWrite": 0
    },
    "metricSamples": {
      "total": 1, "input": 1, "output": 1,
      "cached": 1, "reasoning": 1, "cacheRead": 1, "cacheWrite": 1
    }
  }]
}
```

- Counts describe **observed upstream execution attempts**, including failures,
  retries and fallbacks, rather than unique client requests. Account attribution
  uses the host's 16-character hexadecimal `AuthIndex`, joined against current
  `/auth-files` metadata by the desktop. Accounts removed later remain in the
  collector's summary until its lifetime state is retired.
- Each token field is copied from the corresponding normalized SDK field:
  `total` = `TotalTokens`, `input` = `InputTokens`, `output` = `OutputTokens`,
  `cached` = `CachedTokens`, `reasoning` = `ReasoningTokens`, `cacheRead` =
  `CacheReadTokens`, `cacheWrite` = `CacheCreationTokens`. **Do not sum all these
  fields**: cache and reasoning may overlap input/output, and cache conventions
  differ between providers. The collector does not invent totals.
- v8's SDK loses field-presence information. An event with any positive token
  counter is treated as a reported token record; the other normalized SDK zeros
  may represent omitted submetrics. If all counters are zero, token metrics are
  unknown for that event, including failed attempts. `metricSamples` counts known
  records for each metric; `tokenMetrics` sums them and is null when none are
  known. A sample count below requests means the sum is partial, not a complete
  total. Provider totals should preserve that coverage in the UI.
- `accounts` remains a **lifetime aggregate since `startedAt`**. Schema 2 also
  exports `buckets` with one row per UTC calendar day, provider, account and
  model. Each row has `date` (`YYYY-MM-DD`), `provider`, `authIndex`, `model`,
  `requests`, `tokenMetrics` and `metricSamples`; the metrics use the same shape
  and accounting rules as account totals above. `Model` is copied from the
  upstream SDK `UsageRecord.Model`, not the user-facing alias or response model.
  Empty models become `unknown`; invalid names also become `unknown` and mark
  history partial. Names must be 1–128 ASCII characters matching
  `[A-Za-z0-9][A-Za-z0-9._:/()+-]{0,127}`.
- History retains **30 UTC calendar days including today**; 1 day means today
  from 00:00 UTC, 7 days means today and the preceding 6 UTC dates. These are not
  rolling 24/168-hour windows. `asOf` is the export time, even on an idle server;
  `historySince` is the time dated/model collection began. Windows crossing
  that timestamp have incomplete coverage. Older events remain in lifetime
  totals without being inserted into current history. Future-day events within
  the SDK clock-skew allowance are omitted from history and marked partial.
- Both aggregates survive clean restarts and never store raw requests. In-flight events can be lost during abrupt termination; an unclean
  restart permanently marks this history `partial: true`. The host does not
  offer replay/acknowledgement to usage plugins, so even `health: ok` means the
  collector is healthy, not that every event was delivered by the host.
- `health: degraded` and `partial: true` persist after rejected events, overflow,
  capacity limits, or write errors; `dropped` counts rejected events. A write error
  retains aggregates in memory and subsequent writes try again, but disk history
  can lag. A disk that cannot write cannot reliably preserve its own failure
  marker; the previous unclean state helps detect this on restart.
- `historyPartial` additionally marks unknown/invalid model attribution and
  history bucket capacity or overflow. `historyDropped` counts events omitted
  from dated history by those capacity/overflow limits. Their lifetime account
  totals continue to accumulate. These coverage markers persist even when old
  buckets expire. The desktop retains removed/unmatched accounts in its global
  and model history totals, without exporting raw account indexes to QML.

## Persistence and limits

The private directory contains atomic mode-0600 `usage.json` snapshots and a
writer lock. Every accepted event is serialized under a mutex and written with
file and directory `fsync`. This favors durable small installations; load-test
before use at high request rates because fsync latency delays the host's usage
observer. Each data directory has one exclusive writer. Changing `data_dir`
requires a server restart, and binary replacement should also use a restart;
concurrent replacement loaders are refused while the old writer owns its lock.

Limits: 1,024 lifetime provider/account pairs, 2,048 dated/model buckets across
all 30 retained UTC dates, 4,096 recent hashed execution IDs for
best-effort duplicate suppression across restarts, 4 MiB persisted JSON, and
JavaScript-safe nonnegative integer counters. Duplicates older than that bounded
ring, or records without a RequestID, cannot be deduplicated. Invalid identity,
timestamps or counters are dropped and mark history partial. State corruption
fails registration and preserves the file; it never silently resets history.

Only opaque auth indexes, provider identifiers, bounded model identifiers,
aggregate counters, timestamps, health metadata and SHA-256 execution-ID hashes
are stored. Model names can reveal which models an installation uses; keep
collector state private. API keys, auth IDs, filenames, account emails, prompts,
response bodies and headers are ignored. Do not place the private data directory inside a source checkout. To
retire history, stop the server, archive the directory privately, configure a new
empty private directory, then restart. There is no automated deletion or reset.


## Upgrade from collector 0.1 / schema 1

Collector 0.2 adds dated/model history. Before replacing the binary, stop the
server through its normal procedure and privately back up its config, collector
binary and data directory. Preserve the same owner and private permissions.
Build and ABI-test the replacement for the deployment's actual architecture and
libc, then restart through the normal procedure. Deploying this repository does
not automatically replace the server collector.

On first load, a valid schema-1 `usage.json` is copied byte-for-byte to private
`usage.v1.backup.json`, flushed to disk, and migrated to schema 2. Lifetime totals,
original `startedAt`, deduplication hashes and coverage warnings are preserved.
Dated/model history begins at upgrade time; the old aggregate cannot be
backfilled into dates or models. Existing schema-2 history survives restarts.
Malformed state, unsafe permissions, a failed backup, or a conflicting existing
backup stop registration and preserve the source file rather than reset totals.
An identical existing backup permits retry after an interrupted migration.

A schema-1 binary cannot read schema-2 state. For rollback, stop the server,
privately archive the entire current state, restore the old binary and the
pre-upgrade `usage.v1.backup.json` as `usage.json` with service ownership and mode
0600, then restart. This restores only the pre-upgrade totals: usage collected
after upgrade is retained in the archive but is not importable by schema 1.
Archive/remove the old migration backup before a later new migration, since a
conflicting backup is intentionally never overwritten.
