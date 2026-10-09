package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func historyCollector(t *testing.T) (*collector, time.Time) {
	t.Helper()
	c := testCollector(t)
	now := time.Date(2026, 10, 8, 12, 0, 0, 0, time.UTC)
	c.now = func() time.Time { return now }
	c.state.StartedAt = now.AddDate(0, 0, -40)
	c.state.HistorySince = c.state.StartedAt
	c.state.UpdatedAt = now
	return c, now
}

func addHistoryEvent(t *testing.T, c *collector, id, model, provider, index string, at time.Time) {
	t.Helper()
	e := fixture(id)
	e.Model, e.Provider, e.AuthIndex, e.RequestedAt = model, provider, index, at
	if err := c.add(e); err != nil {
		t.Fatal(err)
	}
}

func TestHistoryDaysModelsProvidersAndRetention(t *testing.T) {
	c, now := historyCollector(t)
	today := utcDay(now)
	addHistoryEvent(t, c, "a", "gpt-test", "codex", "0123456789abcdef", today)
	addHistoryEvent(t, c, "b", "gpt-test", "codex", "0123456789abcdef", today.Add(time.Hour))
	addHistoryEvent(t, c, "c", "gpt-other", "codex", "0123456789abcdef", today)
	addHistoryEvent(t, c, "d", "gpt-test", "claude", "0123456789abcdef", today)
	addHistoryEvent(t, c, "e", "gpt-test", "codex", "fedcba9876543210", today)
	addHistoryEvent(t, c, "f", "gpt-test", "codex", "0123456789abcdef", today.Add(-time.Nanosecond))
	addHistoryEvent(t, c, "g", "gpt-test", "codex", "0123456789abcdef", today.AddDate(0, 0, -29))
	addHistoryEvent(t, c, "h", "gpt-test", "codex", "0123456789abcdef", today.AddDate(0, 0, -29).Add(-time.Nanosecond))
	if len(c.state.Buckets) != 6 {
		t.Fatalf("expected 6 buckets, got %d", len(c.state.Buckets))
	}
	key := "2026-10-08|codex|0123456789abcdef|gpt-test"
	if c.state.Buckets[key].Requests != 2 || *c.state.Buckets[key].TokenMetrics["total"] != 28 {
		t.Fatal("model aggregate incorrect")
	}
	if c.state.Accounts["codex:0123456789abcdef"].Requests != 6 {
		t.Fatal("expired event lost from lifetime")
	}
	// A timestamp with a local offset is assigned to its UTC day.
	at, _ := time.Parse(time.RFC3339, "2026-10-07T23:30:00-04:00")
	addHistoryEvent(t, c, "offset", "gpt-test", "codex", "0123456789abcdef", at)
	if c.state.Buckets[key].Requests != 3 {
		t.Fatal("bucket used local calendar date")
	}
	c.now = func() time.Time { return now.AddDate(0, 0, 1) }
	summary := c.summary() // Idle collectors age history out without new requests.
	if len(c.state.Buckets) != 5 || summary["asOf"] != now.AddDate(0, 0, 1) {
		t.Fatal("idle retention or asOf incorrect")
	}
	if summary["historyPartial"] != false {
		t.Fatal("normal retention marks history partial")
	}
}

func TestHistoryUnknownModelTokensDedupAndRestart(t *testing.T) {
	c := testCollector(t)
	now := time.Now().UTC()
	e := fixture("unknown")
	e.RequestedAt, e.Detail = now, usageDetail{}
	if err := c.add(e); err != nil {
		t.Fatal(err)
	}
	_ = c.add(e)
	b := c.state.Buckets[now.Format("2006-01-02")+"|codex|0123456789abcdef|unknown"]
	if b.Requests != 1 || b.TokenMetrics["total"] != nil || b.MetricSamples["total"] != 0 {
		t.Fatal("unknown tokens invented or duplicate recorded")
	}
	addHistoryEvent(t, c, "badmodel", "private@example.com", "codex", "0123456789abcdef", now)
	if !c.state.HistoryPartial || b.Requests != 2 || *b.TokenMetrics["total"] != 14 {
		t.Fatal("invalid model not safely represented")
	}
	raw, _ := os.ReadFile(filepath.Join(c.dir, "usage.json"))
	if bytes.Contains(raw, []byte("private@example.com")) {
		t.Fatal("invalid model persisted")
	}
	c.close()
	restored, err := openCollector(c.dir)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.close()
	if len(restored.state.Buckets) != 1 || restored.state.Buckets[bucketKey(b)].Requests != 2 {
		t.Fatal("history not persisted")
	}
}

func TestSchema1MigrationPreservesExactBackupAndTotals(t *testing.T) {
	c := testCollector(t)
	_ = c.add(fixture("prior"))
	c.state.SchemaVersion = 1
	c.state.HistorySince = time.Time{}
	c.state.Buckets = nil
	c.close()
	path := filepath.Join(c.dir, "usage.json")
	before, _ := os.ReadFile(path)
	originalStarted := c.state.StartedAt
	restored, err := openCollector(c.dir)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.close()
	backup, err := os.ReadFile(filepath.Join(c.dir, "usage.v1.backup.json"))
	if err != nil || !bytes.Equal(before, backup) {
		t.Fatal("original migration state was not preserved exactly")
	}
	if restored.state.SchemaVersion != 2 || restored.state.StartedAt != originalStarted || restored.state.HistorySince.Before(originalStarted) || len(restored.state.Buckets) != 0 || restored.state.Accounts["codex:0123456789abcdef"].Requests != 1 {
		t.Fatal("migration reset or fabricated history")
	}
	_ = restored.add(fixture("after"))
	if len(restored.state.Buckets) != 1 || restored.state.Accounts["codex:0123456789abcdef"].Requests != 2 {
		t.Fatal("new observations not recorded after migration")
	}
	restored.close()
	info, _ := os.Stat(filepath.Join(c.dir, "usage.v1.backup.json"))
	if !privateOwned(info, false) {
		t.Fatal("backup permissions unsafe")
	}
	again, err := openCollector(c.dir)
	if err != nil {
		t.Fatal(err)
	}
	defer again.close()
	if !again.state.HistorySince.Equal(restored.state.HistorySince) {
		t.Fatal("coverage start changed on restart")
	}
	// A mismatching backup is preserved and blocks a new migration.
	again.close()
	_ = os.WriteFile(path, before, 0600)
	_ = os.WriteFile(filepath.Join(c.dir, "usage.v1.backup.json"), []byte("different"), 0600)
	failed, err := openCollector(c.dir)
	if err == nil {
		failed.close()
		t.Fatal("mismatched backup overwritten")
	}
	after, _ := os.ReadFile(path)
	if !bytes.Equal(before, after) {
		t.Fatal("failed migration altered source")
	}
}

func TestHistoryCapacityAndOverflowPreserveLifetime(t *testing.T) {
	c, now := historyCollector(t)
	for i := 0; i < maxHistoryBuckets; i++ {
		b := &historyBucket{Date: "2026-10-08", Provider: "codex", AuthIndex: "0123456789abcdef", Model: fmt.Sprintf("model-%d", i), Requests: 1, TokenMetrics: map[string]*int64{}, MetricSamples: map[string]int64{}}
		for _, k := range metricNames {
			b.TokenMetrics[k] = nil
			b.MetricSamples[k] = 0
		}
		c.state.Buckets[bucketKey(b)] = b
	}
	addHistoryEvent(t, c, "full", "new-model", "codex", "0123456789abcdef", now)
	if len(c.state.Buckets) != maxHistoryBuckets || c.state.HistoryDropped != 1 || c.state.Accounts["codex:0123456789abcdef"].Requests != 1 || c.summary()["historyPartial"] != true {
		t.Fatal("capacity lost lifetime or hid missing history")
	}
	b := c.state.Buckets["2026-10-08|codex|0123456789abcdef|model-0"]
	b.TokenMetrics["total"] = new(int64)
	*b.TokenMetrics["total"] = maxCounter
	b.MetricSamples["total"] = 1
	addHistoryEvent(t, c, "overflow", "model-0", "codex", "0123456789abcdef", now)
	if b.Requests != 1 || *b.TokenMetrics["total"] != maxCounter || c.state.HistoryDropped != 2 || c.state.Accounts["codex:0123456789abcdef"].Requests != 2 {
		t.Fatal("overflow was not atomic and explicit")
	}
	c.now = func() time.Time { return now.AddDate(0, 0, 30) }
	addHistoryEvent(t, c, "recovered", "gpt-test", "codex", "0123456789abcdef", now.AddDate(0, 0, 30))
	if len(c.state.Buckets) != 1 || !c.state.HistoryPartial {
		t.Fatal("capacity recovery or coverage incorrect")
	}
}

func TestHistoryStateValidationAndMaximumSnapshotSize(t *testing.T) {
	c, now := historyCollector(t)
	addHistoryEvent(t, c, "valid", "gpt-test", "codex", "0123456789abcdef", now)
	b := c.state.Buckets["2026-10-08|codex|0123456789abcdef|gpt-test"]
	b.Date = "2026-99-99"
	if validState(c.state) {
		t.Fatal("invalid persisted date accepted")
	}
	c.state.Buckets = map[string]*historyBucket{}
	c.state.Accounts = map[string]*account{}
	family := strings.Repeat("a", 48)
	values, samples := map[string]*int64{}, map[string]int64{}
	for _, k := range metricNames {
		n := maxCounter
		values[k] = &n
		samples[k] = maxCounter
	}
	for i := 0; i < maxAccounts; i++ {
		a := &account{AuthIndex: fmt.Sprintf("%016x", i), Provider: family, Requests: maxCounter, Failed: maxCounter, FirstRequestAt: now, LastRequestAt: now, TokenMetrics: values, MetricSamples: samples}
		c.state.Accounts[a.Provider+":"+a.AuthIndex] = a
	}
	for i := 0; i < maxHistoryBuckets; i++ {
		b := &historyBucket{Date: "2026-10-08", Provider: family, AuthIndex: "0123456789abcdef", Model: fmt.Sprintf("%0128d", i), Requests: maxCounter, TokenMetrics: values, MetricSamples: samples}
		c.state.Buckets[bucketKey(b)] = b
	}
	c.state.Seen = nil
	for i := 0; i < maxDedup; i++ {
		c.state.Seen = append(c.state.Seen, fmt.Sprintf("%064x", i))
	}
	if !validState(c.state) {
		t.Fatal("valid maximum state rejected")
	}
	raw, _ := json.Marshal(c.state)
	summary, _ := json.Marshal(c.summary())
	if len(raw) > maxState || len(summary) > 8*1024*1024 {
		t.Fatalf("snapshot exceeds transport/storage limits: state=%d summary=%d", len(raw), len(summary))
	}
	t.Logf("maximum state %d bytes; summary %d bytes", len(raw), len(summary))
}
