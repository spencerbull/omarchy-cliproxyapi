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
        return delta || (sort === "provider" ? accountName(a, privateMode).localeCompare(accountName(b, privateMode)) : 0) || String(a.id).localeCompare(String(b.id))
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

function resetShort(value, now) {
    if (!value || !isFinite(Date.parse(value))) return "—"
    var minutes = Math.ceil((Date.parse(value) - now) / 60000)
    if (minutes <= 0) return "Due"
    if (minutes < 60) return minutes + "m"
    if (minutes < 1440) return Math.floor(minutes / 60) + "h" + (minutes % 60 ? minutes % 60 + "m" : "")
    return Math.floor(minutes / 1440) + "d" + (Math.floor(minutes % 1440 / 60) ? Math.floor(minutes % 1440 / 60) + "h" : "")
}
function shortDate(value) {
    if (!value || !isFinite(Date.parse(value))) return ""
    var date = new Date(value)
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][date.getMonth()] + " " + date.getDate()
}
function money(cents) { return typeof cents === "number" && isFinite(cents) ? "$" + (cents / 100).toFixed(2) : "—" }
function quotaFacts(quota) {
    if (!quota) return ""
    var facts = [], credits = quota.credits || {}, extra = quota.extraUsage || {}
    if (credits.unlimited === true) facts.push("Unlimited credits")
    else if (credits.balance !== null && credits.balance !== undefined) facts.push(compact(credits.balance) + " credits")
    if (credits.resetCreditsAvailable !== null && credits.resetCreditsAvailable !== undefined) facts.push((credits.resetCreditsApplicable !== null && credits.resetCreditsApplicable !== undefined ? count(credits.resetCreditsApplicable) + "/" : "") + count(credits.resetCreditsAvailable) + (credits.resetCreditsApplicable !== null && credits.resetCreditsApplicable !== undefined ? " resets usable" : " resets reported"))
    if (quota.renewalAt) facts.push("Renews " + shortDate(quota.renewalAt))
    if (extra.enabled === false) facts.push("Extra usage off")
    else if (extra.usedCredits !== null && extra.usedCredits !== undefined) {
        var used = extra.unit === "USD cents" ? money(extra.usedCredits) : count(extra.usedCredits)
        var cap = extra.unit === "USD cents" ? money(extra.monthlyLimit) : count(extra.monthlyLimit)
        facts.push("Extra " + used + (extra.monthlyLimit !== null && extra.monthlyLimit !== undefined ? " / " + cap : ""))
    }
    if (extra.prepaidBalance !== null && extra.prepaidBalance !== undefined) facts.push(money(extra.prepaidBalance) + " prepaid")
    if (quota.subscriptionActive === false) facts.push("Subscription inactive")
    return facts.join(" · ")
}
function windowState(window) {
    return window.isActive === false ? "Inactive" : window.allowed === false || window.limitReached === true ? "Blocked" : ""
}
function quotaWindows(quota) {
    if (!quota) return []
    var windows = (quota.windows || []).slice(), extra = quota.extraUsage
    if (extra && extra.enabled === true && extra.usedPercent !== null && extra.usedPercent !== undefined)
        windows.push({label:"Extra usage", usedPercent:extra.usedPercent, resetAt:null})
    return windows
}
function quotaDetails(quota) {
    if (!quota) return ""
    var rows = []
    if (quota.renewalAt) rows.push("Renewal: " + new Date(quota.renewalAt).toLocaleString())
    var credits = quota.credits || {}
    if (credits.balance !== null && credits.balance !== undefined) rows.push("Credit balance: " + credits.balance)
    if (credits.resetCreditsApplicable !== null && credits.resetCreditsApplicable !== undefined) rows.push(count(credits.resetCreditsApplicable) + " applicable resets")
    ;(quota.resetCredits || []).forEach(function(credit, index) {
        rows.push("Reset " + (index + 1) + " expires " + (credit.expiresAt ? new Date(credit.expiresAt).toLocaleString() : "at an unreported time") + (credit.applicable === false ? " (not applicable)" : ""))
    })
    var extra = quota.extraUsage || {}
    if (extra.onDemandUsed !== null && extra.onDemandUsed !== undefined) rows.push("On-demand " + money(extra.onDemandUsed) + " / " + money(extra.onDemandLimit))
    return rows.join("\n")
}
function retryWait(quota, now) {
    if (!quota) return 0
    var last = Date.parse(quota.lastAttemptAt || quota.updatedAt || "")
    return isFinite(last) ? Math.max(0, last + Math.max(0, Number(quota.retryAfter) || 0) * 1000 - now) : 0
}
function dueQuotaIds(accounts, quotas, consented, now, force) {
    return accounts.filter(function(account) {
        if (!account.quotaSupported || (account.quotaConsentRequired && consented[account.id] !== true)) return false
        var cached = quotas[account.id]
        if (!cached) return true
        var last = Date.parse(cached.lastAttemptAt || cached.updatedAt || "")
        var retryDelay = Math.max(0, Number(cached.retryAfter) || 0) * 1000
        if (isFinite(last) && now < last + retryDelay) return false
        return force || !isFinite(last) || now - last >= 300000
    }).map(function(account) { return account.id })
}
function mergeQuota(previous, message) {
    var merged = Object.assign({}, message, {lastAttemptAt:message.updatedAt})
    if (message.error && previous && !previous.consentRequired) {
        merged = Object.assign({}, previous, {error:message.error, lastAttemptAt:message.updatedAt, retryAfter:message.retryAfter || 0})
    }
    return merged
}

if (typeof module !== "undefined") module.exports = {windowState:windowState, retryWait:retryWait, resetShort:resetShort, quotaFacts:quotaFacts, quotaWindows:quotaWindows, quotaDetails:quotaDetails, dueQuotaIds:dueQuotaIds, mergeQuota:mergeQuota, retainedQuotas: retainedQuotas, count: count, compact: compact, relative: relative, reset: reset, activity: activity, filtered: filtered, providers: providers, summary: summary}
