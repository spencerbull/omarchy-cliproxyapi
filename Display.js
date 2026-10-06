function count(value) {
    if (value === null || value === undefined || !isFinite(value)) return "—"
    return Math.round(value).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")
}
function compact(value) {
    if (value === null || value === undefined) return "—"
    return value >= 1000000 ? (value / 1000000).toFixed(1) + "m" : value >= 1000 ? (value / 1000).toFixed(1) + "k" : count(value)
}
function providerName(value) {
    var names = {meta: "Muse", xai: "xAI", codex: "Codex", claude: "Claude", gemini: "Gemini", antigravity: "Antigravity", openai: "OpenAI", "github-copilot": "Copilot", "kimi-ai": "Kimi", custom: "Custom"}
    return names[value] || (value ? value.charAt(0).toUpperCase() + value.slice(1) : "Unknown")
}
function relative(value, now) {
    if (!value || !isFinite(Date.parse(value))) return "Not reported"
    var seconds = Math.max(0, Math.floor((now - Date.parse(value)) / 1000))
    if (seconds < 60) return "Just now"
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago"
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago"
    return Math.floor(seconds / 86400) + "d ago"
}
function reset(value, now) {
    if (!value || !isFinite(Date.parse(value))) return "Reset not reported"
    var minutes = Math.ceil((Date.parse(value) - now) / 60000)
    if (minutes <= 0) return "Reset due · refresh"
    if (minutes < 60) return "Resets in " + minutes + "m"
    if (minutes < 1440) return "Resets in " + Math.floor(minutes / 60) + "h " + (minutes % 60) + "m"
    return "Resets in " + Math.floor(minutes / 1440) + "d " + Math.floor((minutes % 1440) / 60) + "h"
}
function activity(account, now) {
    if (account.lastActivityKind === "exact" && account.lastRequestAt) return relative(account.lastRequestAt, now)
    if (account.lastActivityKind === "window" && account.lastActivityLabel) return account.lastActivityLabel.replace(/-/g, "–")
    return "Not reported"
}
function accountName(account, privateMode) {
    return privateMode ? "Account " + String(account.id || "").slice(-4) : (account.label || "Account")
}
function filtered(accounts, provider, query, sort, privateMode) {
    var needle = String(query || "").toLowerCase().trim()
    return accounts.filter(function(account) {
        return (provider === "all" || account.provider === provider)
            && (!needle || (accountName(account, privateMode) + " " + providerName(account.provider) + " " + (account.plan || "")).toLowerCase().indexOf(needle) !== -1)
    }).slice().sort(function(a, b) {
        var delta = sort === "requests" ? ((b.requests === null ? -1 : b.requests) - (a.requests === null ? -1 : a.requests))
            : sort === "provider" ? String(a.provider).localeCompare(String(b.provider))
            : (Number(b.lastActivityRank || 0) - Number(a.lastActivityRank || 0))
        return delta || String(a.id).localeCompare(String(b.id))
    })
}
function providers(accounts) {
    var values = {}
    accounts.forEach(function(account) { values[account.provider] = (values[account.provider] || 0) + 1 })
    return [{value: "all", label: "All", count: accounts.length}].concat(Object.keys(values).sort().map(function(key) {
        return {value: key, label: providerName(key), count: values[key]}
    }))
}
function summary(accounts) {
    var total = 0, known = 0, failed = 0, failedKnown = 0, ready = 0
    accounts.forEach(function(account) {
        if (account.requests !== null && account.requests !== undefined) { total += account.requests; known++ }
        if (account.failed !== null && account.failed !== undefined) { failed += account.failed; failedKnown++ }
        if (account.status === "active") ready++
    })
    return {requests: known ? total : null, failed: failedKnown ? failed : null, ready: ready, partial: known < accounts.length}
}

// Credential-file slots can be reused for a different signed-in account.
function retainedQuotas(quotas, before, after) {
    var retained = {}
    after.forEach(function(account) {
        var old = before.find(function(item) { return item.id === account.id })
        if (old && quotas[account.id] && ["label", "provider", "kind", "plan"].every(function(key) { return old[key] === account[key] }))
            retained[account.id] = quotas[account.id]
    })
    return retained
}

if (typeof module !== "undefined") module.exports = {retainedQuotas: retainedQuotas, count: count, compact: compact, relative: relative, reset: reset, activity: activity, filtered: filtered, providers: providers, summary: summary}
