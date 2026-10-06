package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

func fixture(id string) usageEvent {
	return usageEvent{RequestID: id, AuthIndex: "0123456789abcdef", Provider: "codex", RequestedAt: time.Now().UTC(), Detail: usageDetail{InputTokens: 10, OutputTokens: 4, CachedTokens: 3, ReasoningTokens: 2, TotalTokens: 14}}
}
func testCollector(t *testing.T) *collector {
	t.Helper()
	c, e := openCollector(filepath.Join(t.TempDir(), "private"))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(c.close)
	return c
}
func TestAggregateUnknownDedupRestart(t *testing.T) {
	c := testCollector(t)
	e := fixture("a")
	if err := c.add(e); err != nil {
		t.Fatal(err)
	}
	_ = c.add(e)
	unknown := fixture("b")
	unknown.Detail = usageDetail{}
	unknown.Failed = true
	unknown.RequestedAt = e.RequestedAt.Add(-time.Hour)
	_ = c.add(unknown)
	a := c.state.Accounts["codex:0123456789abcdef"]
	if a.Requests != 2 || a.Failed != 1 || *a.TokenMetrics["total"] != 14 || *a.TokenMetrics["cacheRead"] != 0 || a.MetricSamples["input"] != 1 || !a.FirstRequestAt.Equal(unknown.RequestedAt) {
		t.Fatalf("bad aggregate: %+v", a)
	}
	dir := c.dir
	c.close()
	restored, err := openCollector(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.close()
	if restored.state.Partial {
		t.Fatal("clean restart reported partial")
	}
	_ = restored.add(e)
	if restored.state.Accounts["codex:0123456789abcdef"].Requests != 2 {
		t.Fatal("dedup not persisted")
	}
}
func TestConcurrentWritesAndSnapshots(t *testing.T) {
	c := testCollector(t)
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if err := c.add(fixture(fmt.Sprint(i))); err != nil {
				t.Error(err)
			}
			_ = c.summary()
		}(i)
	}
	wg.Wait()
	if c.state.Accounts["codex:0123456789abcdef"].Requests != 50 {
		t.Fatal("lost concurrent events")
	}
}
func TestPrivacyAndRoute(t *testing.T) {
	closeCollector()
	t.Cleanup(closeCollector)
	config, _ := json.Marshal(map[string]any{"config_yaml": []byte("data_dir: " + filepath.Join(t.TempDir(), "private"))})
	if _, e := handleMethod("plugin.register", config); e != nil {
		t.Fatal(e)
	}
	payload := `{"RequestID":"sensitive-request","AuthIndex":"0123456789abcdef","Provider":"claude","AuthID":"private-file.json","APIKey":"private-key","Model":"private-model","ResponseHeaders":{"Authorization":["private-token"]},"Failure":{"Body":"private-body"},"RequestedAt":"` + time.Now().UTC().Format(time.RFC3339Nano) + `","Detail":{"InputTokens":12,"TotalTokens":12}}`
	if _, e := handleMethod("usage.handle", []byte(payload)); e != nil {
		t.Fatal(e)
	}
	raw, e := os.ReadFile(filepath.Join(running.dir, "usage.json"))
	if e != nil {
		t.Fatal(e)
	}
	summary, e := handleMethod("management.handle", []byte(`{"Method":"GET","Path":"`+summaryPath+`"}`))
	if e != nil {
		t.Fatal(e)
	}
	var env struct{ Result struct{ Body []byte } }
	_ = json.Unmarshal(summary, &env)
	for _, s := range []string{"sensitive-request", "private-file", "private-key", "private-model", "private-token", "private-body"} {
		if strings.Contains(string(raw), s) || strings.Contains(string(env.Result.Body), s) {
			t.Fatalf("leaked %s", s)
		}
	}
	routes, _ := handleMethod("management.register", []byte(`{}`))
	if strings.Contains(string(routes), "resources") || strings.Contains(string(routes), "Menu") {
		t.Fatal("unauthenticated route advertised")
	}
	denied, _ := handleMethod("management.handle", []byte(`{"Method":"POST","Path":"`+summaryPath+`"}`))
	if !strings.Contains(string(denied), `"StatusCode":404`) {
		t.Fatal("unexpected write route")
	}
	for _, path := range []string{running.dir, filepath.Join(running.dir, "usage.json")} {
		st, _ := os.Stat(path)
		if st.Mode().Perm()&0077 != 0 {
			t.Fatal("state is not private")
		}
	}
}
func TestMalformedAndBounds(t *testing.T) {
	c := testCollector(t)
	bad := fixture("bad")
	bad.AuthIndex = "private@example.com"
	_ = c.add(bad)
	negative := fixture("negative")
	negative.Detail.InputTokens = -1
	_ = c.add(negative)
	future := fixture("future")
	future.RequestedAt = time.Now().Add(time.Hour)
	_ = c.add(future)
	if len(c.state.Accounts) != 0 || c.state.Dropped != 3 || !c.state.Partial {
		t.Fatal("invalid events not rejected")
	}
	e := fixture("valid")
	e.Detail.InputTokens = maxCounter
	e.Detail.TotalTokens = maxCounter
	_ = c.add(e)
	_ = c.add(fixture("overflow"))
	if c.state.Dropped != 4 || c.state.Accounts["codex:0123456789abcdef"].Requests != 1 {
		t.Fatal("overflow not rejected atomically")
	}
}
func TestUnknownNotZero(t *testing.T) {
	c := testCollector(t)
	e := fixture("unknown")
	e.Detail = usageDetail{}
	_ = c.add(e)
	a := c.state.Accounts["codex:0123456789abcdef"]
	for _, k := range metricNames {
		if a.TokenMetrics[k] != nil || a.MetricSamples[k] != 0 {
			t.Fatal("invented zero")
		}
	}
}
func TestStorageFailureAndUncleanRestart(t *testing.T) {
	c := testCollector(t)
	original := c.dir
	c.dir = filepath.Join(original, "missing")
	if c.add(fixture("write-failure")) == nil {
		t.Fatal("write failure ignored")
	}
	if c.summary()["health"] != "degraded" {
		t.Fatal("failure hidden")
	}
	c.dir = original
	_ = c.add(fixture("recovered"))
	if !c.state.Partial {
		t.Fatal("failure history erased")
	}
	c.closeLock() // simulate interruption without clean marker
	restored, e := openCollector(original)
	if e != nil {
		t.Fatal(e)
	}
	defer restored.close()
	if !restored.state.Partial {
		t.Fatal("unclean restart not marked")
	}
}
func TestInvalidStateLockAndPrivateDirectory(t *testing.T) {
	c := testCollector(t)
	if second, e := openCollector(c.dir); e == nil {
		second.close()
		t.Fatal("second writer allowed")
	}
	dir := t.TempDir()
	_ = os.Chmod(dir, 0755)
	if second, e := openCollector(dir); e == nil {
		second.close()
		t.Fatal("public storage allowed")
	}
	invalid := t.TempDir()
	_ = os.Chmod(invalid, 0700)
	path := filepath.Join(invalid, "usage.json")
	_ = os.WriteFile(path, []byte(`{"schemaVersion":999}`), 0600)
	if second, e := openCollector(invalid); e == nil {
		second.close()
		t.Fatal("invalid state reset silently")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != `{"schemaVersion":999}` {
		t.Fatal("invalid state overwritten")
	}
	link := filepath.Join(t.TempDir(), "link")
	_ = os.Symlink(t.TempDir(), link)
	if second, e := openCollector(link); e == nil {
		second.close()
		t.Fatal("symlink allowed")
	}
}
func TestAccountCap(t *testing.T) {
	c := testCollector(t)
	// Seed the bounded map to test the boundary without thousands of disk writes.
	for i := 0; i < maxAccounts; i++ {
		id := fmt.Sprintf("%016x", i)
		a := &account{AuthIndex: id, Provider: "codex"}
		c.state.Accounts["codex:"+id] = a
	}
	if err := c.add(fixture("over-cap")); err != nil {
		t.Fatal(err)
	}
	if len(c.state.Accounts) != maxAccounts || c.state.Dropped != 1 {
		t.Fatal("account cap bypassed")
	}
}

func TestDedupBoundAndRejectedWire(t *testing.T) {
	c := testCollector(t)
	// Seed recent hashes to exercise eviction without thousands of fsyncs.
	for i := 0; i < maxDedup; i++ {
		id := fmt.Sprintf("%064x", i)
		c.state.Seen = append(c.state.Seen, id)
		c.seen[id] = true
	}
	if err := c.add(fixture("new-execution")); err != nil {
		t.Fatal(err)
	}
	if len(c.seen) != maxDedup || len(c.state.Seen) != maxDedup || c.seen[fmt.Sprintf("%064x", 0)] {
		t.Fatal("dedup ring unbounded or oldest not evicted")
	}
	closeCollector()
	runtimeMu.Lock()
	running = c
	runtimeMu.Unlock()
	t.Cleanup(closeCollector)
	if _, err := handleMethod("usage.handle", []byte(`{"Detail":{"InputTokens":"invalid"}}`)); err == nil {
		t.Fatal("bad wire accepted")
	}
	if c.state.Dropped != 1 || !c.state.Partial {
		t.Fatal("wire rejection missing from health")
	}
}

type statInfo struct {
	os.FileInfo
	stat syscall.Stat_t
}

func (s statInfo) Sys() any { return &s.stat }
func TestRejectForeignOwnerAndHardLinks(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "private")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	info, _ := os.Stat(dir)
	original := *info.Sys().(*syscall.Stat_t)
	foreign := statInfo{FileInfo: info, stat: original}
	foreign.stat.Uid = uint32(os.Geteuid() + 1)
	if privateOwned(foreign, true) {
		t.Fatal("foreign-owned directory accepted")
	}
	path := filepath.Join(dir, "usage.json")
	_ = os.WriteFile(path, []byte(`{}`), 0600)
	fileInfo, _ := os.Stat(path)
	other := statInfo{FileInfo: fileInfo, stat: *fileInfo.Sys().(*syscall.Stat_t)}
	other.stat.Uid = uint32(os.Geteuid() + 1)
	if privateOwned(other, false) {
		t.Fatal("foreign-owned file accepted")
	}
	if err := os.Link(path, filepath.Join(dir, "copy")); err != nil {
		t.Fatal(err)
	}
	if c, err := openCollector(dir); err == nil {
		c.close()
		t.Fatal("hard-linked state accepted")
	}
	_ = os.Remove(path)
	_ = os.Remove(filepath.Join(dir, "copy"))
	_ = os.Remove(filepath.Join(dir, ".collector.lock"))
	lock := filepath.Join(dir, ".collector.lock")
	_ = os.WriteFile(lock, nil, 0600)
	_ = os.Link(lock, filepath.Join(dir, "lock-copy"))
	if c, err := openCollector(dir); err == nil {
		c.close()
		t.Fatal("hard-linked lock accepted")
	}
}
