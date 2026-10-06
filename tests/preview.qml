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
    Plugin.Service { id: backend }
    Plugin.Panel { visible: false }
    FloatingWindow {
        title: "CLIProxyAPI Preview"
        implicitWidth: 530; implicitHeight: 710
        minimumSize: Qt.size(530, 710)
        maximumSize: Qt.size(530, 710)
        color: Color.popups.background
        Plugin.Dashboard {
            id: dashboard
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, Style.space(480))
            height: Math.min(parent.height - 48, Style.space(660))
            service: backend
        }
    }
    IpcHandler {
        target: "preview"
        function connectDemo(): string { preview.connectDemo(); return "submitted demo; password cleared=" + (preview.find(dashboard, "managementKey").text === "") }
        function expand(index: int): void { dashboard.expandedId = backend.snapshot.accounts[index].id }
        function quota(index: int): void { backend.checkQuota(backend.snapshot.accounts[index].id) }
        function filter(provider: string): void { dashboard.selectedProvider = provider }
        function scrollTo(value: int): void { preview.find(dashboard, "accountScroll").contentItem.contentY = value }
        function search(value: string): void { preview.find(dashboard, "accountSearch").text = value }
        function privacy(): void { dashboard.privateMode = !dashboard.privateMode }
        function settings(): void { dashboard.settingsOpen = !dashboard.settingsOpen }
        function forget(): void { backend.forget() }
        function status(): string {
            return JSON.stringify({ready: backend.ready, busy: backend.busy, error: backend.error,
                accounts: backend.snapshot ? backend.snapshot.accounts.length : 0,
                visible: dashboard.visibleAccounts.length, expanded: dashboard.expandedId !== "",
                quotaCount: Object.keys(backend.quotas).length, setup: dashboard.setup})
        }
    }
}
