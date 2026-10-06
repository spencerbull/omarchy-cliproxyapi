package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"sync"
	"syscall"
	"time"
)

const maxAccounts = 1024
const maxDedup = 4096
const maxState = 4 << 20
const maxCounter int64 = 9007199254740991 // Safe in the desktop JavaScript client.
var indexPattern = regexp.MustCompile(`^[a-f0-9]{16}$`)
var providerPattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,47}$`)
var metricNames = []string{"total", "input", "output", "cached", "reasoning", "cacheRead", "cacheWrite"}
var errStorage = errors.New("collector storage unavailable")

type usageDetail struct {
	InputTokens, OutputTokens, CachedTokens, ReasoningTokens, CacheReadTokens, CacheCreationTokens, TotalTokens int64
}
type usageEvent struct {
	RequestID   string
	AuthIndex   string
	Provider    string
	RequestedAt time.Time
	Failed      bool
	Detail      usageDetail
}
type account struct {
	AuthIndex      string            `json:"authIndex"`
	Provider       string            `json:"provider"`
	Requests       int64             `json:"requests"`
	Failed         int64             `json:"failed"`
	FirstRequestAt time.Time         `json:"firstRequestAt"`
	LastRequestAt  time.Time         `json:"lastRequestAt"`
	TokenMetrics   map[string]*int64 `json:"tokenMetrics"`
	MetricSamples  map[string]int64  `json:"metricSamples"`
}
type state struct {
	SchemaVersion int                 `json:"schemaVersion"`
	StartedAt     time.Time           `json:"startedAt"`
	UpdatedAt     time.Time           `json:"updatedAt"`
	Partial       bool                `json:"partial"`
	Dropped       int64               `json:"dropped"`
	Clean         bool                `json:"clean"`
	Accounts      map[string]*account `json:"accounts"`
	Seen          []string            `json:"seen"`
}
type collector struct {
	mu        sync.Mutex
	dir       string
	lock      *os.File
	state     state
	seen      map[string]bool
	storageOK bool
}

// Private files must belong to this process even when the server runs as root.
func privateOwned(info os.FileInfo, directory bool) bool {
	if info == nil {
		return false
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return false
	}
	if directory {
		return info.IsDir() && info.Mode().Perm() == 0700
	}
	return info.Mode().IsRegular() && info.Mode().Perm() == 0600 && stat.Nlink == 1
}

func openCollector(dir string) (*collector, error) {
	if !filepath.IsAbs(dir) || filepath.Clean(dir) != dir {
		return nil, errors.New("data_dir must be an absolute clean path")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, errStorage
	}
	fi, err := os.Lstat(dir)
	if err != nil || !privateOwned(fi, true) {
		return nil, errors.New("data_dir must be a private directory (0700) owned by the service user")
	}
	// Refuse symlink components, including an existing private leaf pointing elsewhere.
	for p := dir; p != "/"; p = filepath.Dir(p) {
		st, e := os.Lstat(p)
		if e != nil || st.Mode()&os.ModeSymlink != 0 {
			return nil, errStorage
		}
	}
	fd, err := syscall.Open(filepath.Join(dir, ".collector.lock"), syscall.O_CREAT|syscall.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, errStorage
	}
	f := os.NewFile(uintptr(fd), "collector-lock")
	lockInfo, statErr := f.Stat()
	if statErr != nil || !privateOwned(lockInfo, false) {
		f.Close()
		return nil, errStorage
	}
	if syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return nil, errors.New("collector data directory already in use")
	}
	c := &collector{dir: dir, lock: f, seen: map[string]bool{}, storageOK: true}
	now := time.Now().UTC()
	c.state = state{SchemaVersion: 1, StartedAt: now, UpdatedAt: now, Accounts: map[string]*account{}, Seen: []string{}}
	path := filepath.Join(dir, "usage.json")
	if fi, e := os.Lstat(path); e == nil {
		if !privateOwned(fi, false) || fi.Size() > maxState {
			c.closeLock()
			return nil, errStorage
		}
		raw, e := os.ReadFile(path)
		if e != nil || json.Unmarshal(raw, &c.state) != nil || !validState(c.state) {
			c.closeLock()
			return nil, errors.New("collector state is invalid; existing data preserved")
		}
		if !c.state.Clean {
			c.state.Partial = true
		}
		for _, id := range c.state.Seen {
			c.seen[id] = true
		}
	} else if !os.IsNotExist(e) {
		c.closeLock()
		return nil, errStorage
	}
	c.state.Clean = false
	if c.persist() != nil {
		c.closeLock()
		return nil, errStorage
	}
	return c, nil
}
func validState(s state) bool {
	if s.SchemaVersion != 1 || s.Accounts == nil || len(s.Accounts) > maxAccounts || len(s.Seen) > maxDedup || s.StartedAt.IsZero() || s.UpdatedAt.Before(s.StartedAt) || s.Dropped < 0 || s.Dropped > maxCounter {
		return false
	}
	for key, a := range s.Accounts {
		if a == nil || !indexPattern.MatchString(a.AuthIndex) || !providerPattern.MatchString(a.Provider) || key != a.Provider+":"+a.AuthIndex || a.FirstRequestAt.IsZero() || a.LastRequestAt.Before(a.FirstRequestAt) || a.Requests < 0 || a.Requests > maxCounter || a.Failed < 0 || a.Failed > a.Requests || a.TokenMetrics == nil || a.MetricSamples == nil || len(a.TokenMetrics) != len(metricNames) || len(a.MetricSamples) != len(metricNames) {
			return false
		}
		for _, k := range metricNames {
			v, ok := a.TokenMetrics[k]
			n, present := a.MetricSamples[k]
			if !ok || !present || n < 0 || n > a.Requests || (v != nil && (*v < 0 || *v > maxCounter)) || (v == nil && n != 0) {
				return false
			}
		}
	}
	for _, id := range s.Seen {
		if len(id) != 64 {
			return false
		}
		if _, e := hex.DecodeString(id); e != nil {
			return false
		}
	}
	return true
}
func (c *collector) persist() error {
	raw, err := json.Marshal(c.state)
	if err != nil || len(raw) > maxState {
		return errStorage
	}
	f, err := os.CreateTemp(c.dir, ".usage-*")
	if err != nil {
		return errStorage
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(raw); err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(name, filepath.Join(c.dir, "usage.json"))
	}
	if err == nil {
		var d *os.File
		d, err = os.Open(c.dir)
		if err == nil {
			err = d.Sync()
			d.Close()
		}
	}
	if err != nil {
		return errStorage
	}
	return nil
}
func (c *collector) save() error {
	if err := c.persist(); err != nil {
		c.storageOK = false
		c.state.Partial = true
		return err
	}
	c.storageOK = true
	return nil
}
func (c *collector) drop() error {
	c.state.Partial = true
	if c.state.Dropped < maxCounter {
		c.state.Dropped++
	}
	return c.save()
}
func (c *collector) add(e usageEvent) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.lock == nil {
		return errStorage
	}
	if !indexPattern.MatchString(e.AuthIndex) || !providerPattern.MatchString(e.Provider) || e.RequestedAt.Before(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)) || e.RequestedAt.After(time.Now().Add(5*time.Minute)) || len(e.RequestID) > 128 {
		return c.drop()
	}
	values := []int64{e.Detail.TotalTokens, e.Detail.InputTokens, e.Detail.OutputTokens, e.Detail.CachedTokens, e.Detail.ReasoningTokens, e.Detail.CacheReadTokens, e.Detail.CacheCreationTokens}
	known := false
	for _, v := range values {
		if v < 0 || v > maxCounter {
			return c.drop()
		}
		known = known || v > 0
	}
	digest := ""
	if e.RequestID != "" {
		hash := sha256.Sum256([]byte(e.RequestID))
		digest = hex.EncodeToString(hash[:])
		if c.seen[digest] {
			return nil
		}
	}
	key := e.Provider + ":" + e.AuthIndex
	a := c.state.Accounts[key]
	if a == nil {
		if len(c.state.Accounts) >= maxAccounts {
			return c.drop()
		}
		a = &account{AuthIndex: e.AuthIndex, Provider: e.Provider, FirstRequestAt: e.RequestedAt, LastRequestAt: e.RequestedAt, TokenMetrics: map[string]*int64{}, MetricSamples: map[string]int64{}}
		for _, k := range metricNames {
			a.TokenMetrics[k] = nil
			a.MetricSamples[k] = 0
		}
	}
	if a.Requests == maxCounter {
		return c.drop()
	}
	for i, k := range metricNames {
		if known && a.TokenMetrics[k] != nil && *a.TokenMetrics[k] > maxCounter-values[i] {
			return c.drop()
		}
	}
	c.state.Accounts[key] = a
	a.Requests++
	if e.Failed {
		a.Failed++
	}
	if e.RequestedAt.Before(a.FirstRequestAt) {
		a.FirstRequestAt = e.RequestedAt
	}
	if e.RequestedAt.After(a.LastRequestAt) {
		a.LastRequestAt = e.RequestedAt
	}
	if known {
		for i, k := range metricNames {
			if a.TokenMetrics[k] == nil {
				a.TokenMetrics[k] = new(int64)
			}
			*a.TokenMetrics[k] += values[i]
			a.MetricSamples[k]++
		}
	}
	if digest != "" {
		c.seen[digest] = true
		c.state.Seen = append(c.state.Seen, digest)
		if len(c.state.Seen) > maxDedup {
			delete(c.seen, c.state.Seen[0])
			c.state.Seen = c.state.Seen[1:]
		}
	}
	c.state.UpdatedAt = time.Now().UTC()
	return c.save()
}
func (c *collector) summary() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	// JSON round-trip produces an immutable snapshot while the lock is held.
	accounts := make([]*account, 0, len(c.state.Accounts))
	for _, a := range c.state.Accounts {
		accounts = append(accounts, a)
	}
	sort.Slice(accounts, func(i, j int) bool {
		return accounts[i].Provider+accounts[i].AuthIndex < accounts[j].Provider+accounts[j].AuthIndex
	})
	raw, _ := json.Marshal(accounts)
	var frozen any
	_ = json.Unmarshal(raw, &frozen)
	health := "ok"
	if !c.storageOK || c.state.Partial {
		health = "degraded"
	}
	return map[string]any{"schemaVersion": 1, "source": "omarchy-usage", "startedAt": c.state.StartedAt, "updatedAt": c.state.UpdatedAt, "coverage": "Observed upstream attempts since collector start", "partial": c.state.Partial, "dropped": c.state.Dropped, "health": health, "notice": "Observed attempts only. SDK-normalized zero counters may mean an omitted submetric. All-zero records have unknown tokens. Cache and reasoning counters may overlap input/output; do not add them to totals.", "accounts": frozen}
}
func (c *collector) closeLock() {
	if c.lock != nil {
		syscall.Flock(int(c.lock.Fd()), syscall.LOCK_UN)
		c.lock.Close()
		c.lock = nil
	}
}
func (c *collector) close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.lock == nil {
		return
	}
	c.state.Clean = true
	if c.save() != nil {
		c.state.Clean = false
	}
	c.closeLock()
}
