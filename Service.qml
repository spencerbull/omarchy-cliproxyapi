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
    property string url: ""
    property string error: ""
    property var snapshot: null
    property var quotas: ({})
    property string quotaAccountId: ""
    property string pendingOperation: ""

    function send(message) {
        if (!ready || busy) return false
        busy = true
        pendingOperation = message.op
        error = ""
        backend.write(JSON.stringify(message) + "\n")
        return true
    }
    function connectTo(address, key, save) {
        return send({op: "connect", url: address, key: key, remember: save})
    }
    function refresh() {
        if (configured) send({op: "refresh"})
    }
    function checkQuota(id) {
        if (send({op: "quota", id: id})) quotaAccountId = id
    }
    function forget() {
        if (send({op: "forget"})) snapshot = null
    }
    function receive(line) {
        var message
        try { message = JSON.parse(line) } catch (_) {
            error = "The local helper returned an invalid response."
            busy = false
            retryable = false
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
            if (!configured) { snapshot = null; quotas = ({}) }
            if (configured) refresh()
        } else if (message.type === "snapshot") {
            if (operation === "connect") quotas = ({})
            else {
                quotas = Display.retainedQuotas(quotas, snapshot ? (snapshot.accounts || []) : [], message.accounts || [])
            }
            snapshot = message
            configured = true
            remember = message.remember === true
            url = message.url || ""
            error = ""
            retryable = true
        } else if (message.type === "quota") {
            if (message.accountId) {
                var updated = Object.assign({}, quotas)
                updated[message.accountId] = message
                quotas = updated
            }
        } else if (message.type === "error") {
            error = message.message || "Unable to reach the proxy."
            configured = message.configured === true
            retryable = message.retryable !== false
        }
    }

    Process {
        id: backend
        command: ["python3", "-u", decodeURIComponent(Qt.resolvedUrl("backend.py").toString().replace(/^file:\/\//, ""))]
        stdinEnabled: true
        running: true
        stdout: SplitParser { onRead: data => root.receive(data) }
        // Upstream payloads and Python diagnostics never enter shell logs.
        stderr: SplitParser { onRead: data => {} }
        onExited: {
            root.ready = false
            root.busy = false
            root.quotaAccountId = ""
            root.error = "The local helper stopped. Reload the plugin to reconnect."
        }
    }
    Timer {
        interval: 60000
        running: root.ready && root.configured && root.retryable
        repeat: true
        onTriggered: root.refresh()
    }
}
