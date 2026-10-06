import QtQuick
import Quickshell
import Quickshell.Io
import "Display.js" as Display

Item {
    id: root
    property var shell: null
    property var manifest: null
    property bool ready: false
    property bool busy: false
    property bool configured: false
    property bool remember: false
    property bool retryable: true
    property bool panelOpen: false
    property string url: ""
    property string error: ""
    property var snapshot: null
    property var quotas: ({})
    property var consentedAccounts: ({})
    property var quotaQueue: []
    property string quotaAccountId: ""
    property string pendingOperation: ""
    property bool forceQuotaRefresh: false
    readonly property bool refreshingLimits: quotaAccountId !== "" || quotaQueue.length > 0

    function send(message) {
        if (!ready || busy) return false
        busy = true
        pendingOperation = message.op
        backend.write(JSON.stringify(message) + "\n")
        return true
    }
    function connectTo(address, key, save) {
        if (busy) return false
        quotaQueue = []
        error = ""
        return send({op: "connect", url: address, key: key, remember: save})
    }
    function refresh(allLimits) {
        if (!configured || busy || refreshingLimits) return
        forceQuotaRefresh = allLimits === true
        send({op: "refresh"})
    }
    function queueLimits(force) {
        if (!snapshot || !retryable) return
        quotaQueue = Display.dueQuotaIds(snapshot.accounts || [], quotas, consentedAccounts, Date.now(), force)
        queueTimer.restart()
    }
    function nextQuota() {
        if (busy || !ready || !configured || !retryable || !quotaQueue.length) return
        var next = quotaQueue.slice()
        var id = next.shift()
        quotaQueue = next
        if (send({op: "quota", id: id, allowKeyIssue: consentedAccounts[id] === true})) quotaAccountId = id
    }
    function checkQuota(id, consent) {
        if (busy || refreshingLimits || !configured || !retryable) return
        if (consent === true) {
            var approved = Object.assign({}, consentedAccounts)
            approved[id] = true
            consentedAccounts = approved
        }
        var account = (snapshot.accounts || []).filter(item => item.id === id)
        quotaQueue = Display.dueQuotaIds(account, quotas, consentedAccounts, Date.now(), true)
        queueTimer.restart()
    }
    function forget() {
        if (busy) return
        quotaQueue = []
        if (send({op: "forget"})) { snapshot = null; quotas = ({}); consentedAccounts = ({}) }
    }
    function receive(line) {
        var message
        try { message = JSON.parse(line) } catch (_) {
            error = "The local helper returned an invalid response."
            busy = false; retryable = false; quotaQueue = []; quotaAccountId = ""
            return
        }
        busy = false
        var operation = pendingOperation
        pendingOperation = ""
        quotaAccountId = ""
        if (message.type === "state") {
            ready = true
            configured = message.configured === true
            remember = message.remember === true
            url = message.url || ""
            error = message.message || ""
            retryable = true
            if (!configured) { snapshot = null; quotas = ({}); quotaQueue = []; consentedAccounts = ({}) }
            if (configured) refresh()
        } else if (message.type === "snapshot") {
            if (operation === "connect") { quotas = ({}); consentedAccounts = ({}) }
            else {
                quotas = Display.retainedQuotas(quotas, snapshot ? (snapshot.accounts || []) : [], message.accounts || [])
                consentedAccounts = Display.retainedQuotas(consentedAccounts, snapshot ? (snapshot.accounts || []) : [], message.accounts || [])
            }
            snapshot = message
            configured = true
            remember = message.remember === true
            url = message.url || ""
            error = ""
            retryable = true
            queueLimits(forceQuotaRefresh)
            forceQuotaRefresh = false
        } else if (message.type === "quota") {
            if (message.accountId) {
                var updated = Object.assign({}, quotas)
                updated[message.accountId] = Display.mergeQuota(quotas[message.accountId], message)
                quotas = updated
            }
            if (message.authFailed === true || message.retryable === false) {
                error = "Management access was rejected. Reconnect in Settings."
                retryable = false; quotaQueue = []
            } else queueTimer.restart()
        } else if (message.type === "error") {
            error = message.message || "Unable to reach the proxy."
            configured = message.configured === true
            retryable = message.retryable !== false
            quotaQueue = []
            forceQuotaRefresh = false
        }
    }

    Process {
        id: backend
        command: ["python3", "-u", decodeURIComponent(Qt.resolvedUrl("backend.py").toString().replace(/^file:\/\//, ""))]
        stdinEnabled: true
        running: true
        stdout: SplitParser { onRead: data => root.receive(data) }
        stderr: SplitParser { onRead: data => {} }
        onExited: {
            root.ready = false; root.busy = false; root.quotaAccountId = ""; root.quotaQueue = []
            root.error = "The local helper stopped. Reload the plugin to reconnect."
        }
    }
    Timer { id: queueTimer; interval: 150; onTriggered: root.nextQuota() }
    Timer {
        interval: 60000
        running: root.ready && root.configured && root.retryable && root.panelOpen
        repeat: true
        onTriggered: root.refresh()
    }
}
