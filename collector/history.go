package main

import (
	"bytes"
	"os"
	"path/filepath"
	"time"
)

func bucketKey(b *historyBucket) string {
	return b.Date + "|" + b.Provider + "|" + b.AuthIndex + "|" + b.Model
}

func validMetrics(requests int64, values map[string]*int64, samples map[string]int64) bool {
	if requests < 1 || requests > maxCounter || len(values) != len(metricNames) || len(samples) != len(metricNames) {
		return false
	}
	for _, k := range metricNames {
		v, ok := values[k]
		n, present := samples[k]
		if !ok || !present || n < 0 || n > requests || (v != nil && (*v < 0 || *v > maxCounter)) || (v == nil) != (n == 0) {
			return false
		}
	}
	return true
}

func validHistory(s state) bool {
	if s.HistorySince.IsZero() || s.HistorySince.Before(s.StartedAt) || s.Buckets == nil || len(s.Buckets) > maxHistoryBuckets || s.HistoryDropped < 0 || s.HistoryDropped > maxCounter {
		return false
	}
	for key, b := range s.Buckets {
		if b == nil || key != bucketKey(b) || !providerPattern.MatchString(b.Provider) || !indexPattern.MatchString(b.AuthIndex) || !modelPattern.MatchString(b.Model) || !validMetrics(b.Requests, b.TokenMetrics, b.MetricSamples) {
			return false
		}
		day, err := time.Parse("2006-01-02", b.Date)
		if err != nil || day.Before(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)) {
			return false
		}
	}
	return true
}

func utcDay(t time.Time) time.Time {
	t = t.UTC()
	return time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
}

func (c *collector) pruneHistory(now time.Time) {
	first := utcDay(now).AddDate(0, 0, -29).Format("2006-01-02")
	for key, b := range c.state.Buckets {
		if b.Date < first {
			delete(c.state.Buckets, key)
		}
	}
}

func (c *collector) historyDrop() {
	c.state.HistoryPartial = true
	if c.state.HistoryDropped < maxCounter {
		c.state.HistoryDropped++
	}
}

func (c *collector) addHistory(e usageEvent, values []int64, known bool) {
	now := c.now()
	c.pruneHistory(now)
	date := utcDay(e.RequestedAt)
	if date.Before(utcDay(now).AddDate(0, 0, -29)) {
		return
	} // Lifetime-only; outside retained period.
	if date.After(utcDay(now)) {
		c.historyDrop()
		return
	}
	model := e.Model
	if model == "" {
		model = "unknown"
	}
	if !modelPattern.MatchString(model) {
		model = "unknown"
		c.state.HistoryPartial = true
	}
	entry := &historyBucket{Date: date.Format("2006-01-02"), Provider: e.Provider, AuthIndex: e.AuthIndex, Model: model}
	key := bucketKey(entry)
	b := c.state.Buckets[key]
	if b == nil {
		if len(c.state.Buckets) >= maxHistoryBuckets {
			c.historyDrop()
			return
		}
		b = entry
		b.TokenMetrics = map[string]*int64{}
		b.MetricSamples = map[string]int64{}
		for _, k := range metricNames {
			b.TokenMetrics[k] = nil
			b.MetricSamples[k] = 0
		}
	}
	if b.Requests == maxCounter {
		c.historyDrop()
		return
	}
	for i, k := range metricNames {
		if known && b.TokenMetrics[k] != nil && *b.TokenMetrics[k] > maxCounter-values[i] {
			c.historyDrop()
			return
		}
	}
	c.state.Buckets[key] = b
	b.Requests++
	if known {
		for i, k := range metricNames {
			if b.TokenMetrics[k] == nil {
				b.TokenMetrics[k] = new(int64)
			}
			*b.TokenMetrics[k] += values[i]
			b.MetricSamples[k]++
		}
	}
}

// A schema-1 binary cannot read schema-2. Never overwrite an existing rollback
// copy: require an operator to resolve a different previous migration snapshot.
func (c *collector) backupV1(raw []byte) error {
	path := filepath.Join(c.dir, "usage.v1.backup.json")
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if os.IsExist(err) {
		info, e := os.Lstat(path)
		if e != nil || !privateOwned(info, false) || info.Size() > maxState {
			return errStorage
		}
		existing, e := os.ReadFile(path)
		if e != nil || !bytes.Equal(raw, existing) {
			return errStorage
		}
		return nil
	}
	if err != nil {
		return errStorage
	}
	if _, err = f.Write(raw); err == nil {
		err = f.Sync()
	}
	if e := f.Close(); err == nil {
		err = e
	}
	if err != nil {
		return errStorage
	}
	d, err := os.Open(c.dir)
	if err != nil {
		return errStorage
	}
	defer d.Close()
	if d.Sync() != nil {
		return errStorage
	}
	return nil
}
