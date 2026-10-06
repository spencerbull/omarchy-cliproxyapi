package main

import (
	"encoding/json"
	"errors"
	"gopkg.in/yaml.v3"
	"sync"
)

const summaryPath = "/v0/management/plugins/omarchy-usage/summary"

var runtimeMu sync.RWMutex
var running *collector

func registration() map[string]any {
	return map[string]any{"schema_version": 6, "metadata": map[string]any{"Name": "omarchy-usage", "Version": "0.1.0", "Author": "spencerbull", "GitHubRepository": "https://github.com/spencerbull/omarchy-cliproxyapi", "ConfigFields": []any{map[string]any{"Name": "data_dir", "Type": "string", "Description": "Required private absolute directory for persistent aggregate usage; change requires server restart."}}}, "capabilities": map[string]bool{"usage_plugin": true, "management_api": true}}
}
func handleMethod(method string, raw []byte) ([]byte, error) {
	switch method {
	case "plugin.register", "plugin.reconfigure":
		var req struct {
			ConfigYAML []byte `json:"config_yaml"`
		}
		if json.Unmarshal(raw, &req) != nil {
			return nil, errors.New("invalid registration")
		}
		var config struct {
			DataDir string `yaml:"data_dir"`
		}
		if yaml.Unmarshal(req.ConfigYAML, &config) != nil {
			return nil, errors.New("invalid collector configuration")
		}
		runtimeMu.Lock()
		defer runtimeMu.Unlock()
		if running != nil {
			if running.dir != config.DataDir {
				return nil, errors.New("changing data_dir requires server restart")
			}
		} else {
			var err error
			running, err = openCollector(config.DataDir)
			if err != nil {
				return nil, err
			}
		}
		return okEnvelope(registration())
	case "management.register":
		return okEnvelope(map[string]any{"routes": []any{map[string]string{"Method": "GET", "Path": summaryPath}}})
	case "usage.handle":
		var event usageEvent
		if json.Unmarshal(raw, &event) != nil {
			markRejected()
			return nil, errors.New("invalid usage event")
		}
		runtimeMu.RLock()
		defer runtimeMu.RUnlock()
		if running == nil {
			return nil, errors.New("collector not configured")
		}
		if err := running.add(event); err != nil {
			return nil, err
		}
		return okEnvelope(struct{}{})
	case "management.handle":
		var req struct{ Method, Path string }
		if json.Unmarshal(raw, &req) != nil {
			return nil, errors.New("invalid summary request")
		}
		if req.Method != "GET" || req.Path != summaryPath {
			return okEnvelope(map[string]any{"StatusCode": 404, "Body": []byte(`{"error":"not found"}`)})
		}
		runtimeMu.RLock()
		defer runtimeMu.RUnlock()
		if running == nil {
			return nil, errors.New("collector not configured")
		}
		body, err := json.Marshal(running.summary())
		if err != nil {
			return nil, errors.New("summary unavailable")
		}
		return okEnvelope(map[string]any{"StatusCode": 200, "Headers": map[string][]string{"Content-Type": {"application/json"}, "Cache-Control": {"no-store"}}, "Body": body})
	case "plugin.quiesce":
		return okEnvelope(struct{}{})
	case "plugin.shutdown":
		closeCollector()
		return okEnvelope(struct{}{})
	default:
		return nil, errors.New("unsupported collector method")
	}
}
func closeCollector() {
	runtimeMu.Lock()
	defer runtimeMu.Unlock()
	if running != nil {
		running.close()
		running = nil
	}
}

func markRejected() {
	runtimeMu.RLock()
	defer runtimeMu.RUnlock()
	if running != nil {
		running.mu.Lock()
		defer running.mu.Unlock()
		_ = running.drop()
	}
}
