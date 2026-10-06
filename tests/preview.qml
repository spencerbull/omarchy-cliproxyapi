import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "PLUGIN_URL" as Plugin

ShellRoot {
    id: preview
    function find(node, name) {
        if (node.objectName === name) return node
        var children = node.children || []
        for (var i = 0; i < children.length; i++) {
            var result = find(children[i], name)
            if (result) return result
        }
        return null
    }
    function connectDemo() {
        find(dashboard, "serverUrl").text = "http://127.0.0.1:" + Quickshell.env("CPA_DEMO_PORT")
        find(dashboard, "managementKey").text = "demo-only"
        dashboard.submit()
    }
    Plugin.Service { id: backend; panelOpen: true }
    Plugin.Panel { visible: false }
    FloatingWindow {
        title: "CLIProxyAPI Preview"
        implicitWidth: 416; implicitHeight: 596
        minimumSize: Qt.size(416, 596)
        maximumSize: Qt.size(416, 596)
        color: Color.popups.background
        Plugin.Dashboard {
            id: dashboard
            anchors.centerIn: parent
            width: Math.min(parent.width - 36, Style.space(380))
            height: Math.min(parent.height - 36, Style.space(560))
            service: backend
        }
    }
    IpcHandler {
        target: "preview"
        function connectDemo(): string { preview.connectDemo(); return "submitted demo; password cleared=" + (preview.find(dashboard, "managementKey").text === "") }
        function expand(index: int): void { dashboard.expandedId = backend.snapshot.accounts[index].id }
        function consent(index: int): void { backend.checkQuota(backend.snapshot.accounts[index].id, true) }
        function quota(index: int): void { backend.checkQuota(backend.snapshot.accounts[index].id) }
        function filter(provider: string): void { dashboard.filterOpen = true; dashboard.selectedProvider = provider }
        function scrollTo(value: int): void { preview.find(dashboard, "accountScroll").contentItem.contentY = value }
        function search(value: string): void { dashboard.filterOpen = true; preview.find(dashboard, "accountSearch").text = value }
        function overview(): void { dashboard.filterOpen = false; dashboard.expandedId = ""; dashboard.resetScroll() }
        function refreshAll(): void { backend.refresh(true) }
        function privacy(): void { dashboard.privateMode = !dashboard.privateMode }
        function settings(): void { dashboard.settingsOpen = !dashboard.settingsOpen }
        function forget(): void { backend.forget() }
        function status(): string {
            return JSON.stringify({ready: backend.ready, busy: backend.busy, error: backend.error,
                accounts: backend.snapshot ? backend.snapshot.accounts.length : 0,
                visible: dashboard.visibleAccounts.length, expanded: dashboard.expandedId !== "",
                quotaCount: Object.keys(backend.quotas).length, queue: backend.quotaQueue.length, setup: dashboard.setup,
                loaded: dashboard.loadedCount, refreshing: backend.refreshingLimits})
        }
    }
}
