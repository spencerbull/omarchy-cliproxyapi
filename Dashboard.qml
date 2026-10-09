import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Display.js" as Display

FocusScope {
    id: root
    required property var service
    signal dismiss()
    property bool settingsOpen: false
    property bool privateMode: false
    property bool connecting: false
    property bool filterOpen: false
    property string activePane: "limits"
    property string selectedProvider: "all"
    property string expandedId: ""
    property double now: Date.now()
    readonly property var snapshotData: service && service.snapshot ? service.snapshot : ({})
    readonly property bool hasData: !!(service && service.snapshot)
    readonly property bool setup: settingsOpen || !hasData
    readonly property var accounts: snapshotData.accounts || []
    readonly property var providerOptions: Display.providers(accounts)
    readonly property var visibleAccounts: Display.filtered(accounts, selectedProvider, search.text, "provider", privateMode)
    readonly property int loadedCount: accounts.filter(account => service && service.quotas[account.id] && !service.quotas[account.id].error).length
    readonly property real preferredHeight: setup ? Style.space(430) : Math.min(Style.space(560), Math.max(Style.space(190), (activePane === "usage" ? usagePane.implicitHeight : accountList.implicitHeight) + Style.space(filterOpen && activePane === "limits" ? 181 : 144)))

    function submit() {
        if (service && service.connectTo(address.text.trim(), secret.text, remember.checked)) {
            secret.clear(); connecting = true
        }
    }
    function toggleAccount(id) { expandedId = expandedId === id ? "" : id }
    function resetScroll() { if (scroll.contentItem) scroll.contentItem.contentY = 0 }
    onPrivateModeChanged: if (privateMode) search.clear()
    onFilterOpenChanged: if (!filterOpen) { search.clear(); selectedProvider = "all" }
    onActivePaneChanged: resetScroll()
    onSelectedProviderChanged: { expandedId = ""; resetScroll() }
    onSetupChanged: {
        resetScroll()
        if (setup) Qt.callLater(function() { address.forceActiveFocus() })
        else secret.clear()
    }
    Keys.onEscapePressed: {
        if (settingsOpen && hasData) settingsOpen = false
        else if (filterOpen) filterOpen = false
        else if (expandedId) expandedId = ""
        else dismiss()
    }
    Connections {
        target: root.service
        function onUrlChanged() { address.text = root.service.url }
        function onRememberChanged() { remember.checked = root.service.remember }
        function onSnapshotChanged() {
            if (root.connecting) { root.settingsOpen = false; root.connecting = false }
            if (!root.accounts.some(account => account.provider === root.selectedProvider)) root.selectedProvider = "all"
            if (!root.accounts.some(account => account.id === root.expandedId)) root.expandedId = ""
        }
        function onErrorChanged() { if (root.service.error) root.connecting = false }
    }
    Component.onCompleted: if (service) { address.text = service.url; remember.checked = service.remember }
    Timer { interval: 15000; repeat: true; running: root.visible; onTriggered: root.now = Date.now() }

    ColumnLayout {
        anchors.fill: parent; spacing: Style.space(10)
        RowLayout {
            Layout.fillWidth: true; spacing: Style.space(6)
            ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(3)
                Label { text: "CLIProxyAPI"; font.pixelSize: Style.font.title; font.bold: true }
                Label { text: root.activePane === "usage" ? "Across all subscriptions" : "Subscription limits"; font.pixelSize: Style.font.caption; color: Qt.alpha(Commons.Color.foreground, 0.5) }
            }
            Item { Layout.fillWidth: true }
            Ui.PanelActionButton {
                iconText: "󰍉"; tooltipText: "Filter subscriptions"; visible: !root.setup && root.activePane === "limits"; focusable: true
                onClicked: { root.filterOpen = !root.filterOpen; if (root.filterOpen) search.forceActiveFocus() }
            }
            Ui.PanelActionButton {
                iconText: root.privateMode ? "󰈉" : "󰈈"; tooltipText: root.privateMode ? "Show account names" : "Hide account names"
                visible: !root.setup; focusable: true; onClicked: root.privateMode = !root.privateMode
            }
            Ui.PanelActionButton {
                iconText: "󰑐"; tooltipText: root.activePane === "usage" ? "Refresh usage" : "Refresh all subscription limits"
                visible: !root.setup; enabled: root.service && !root.service.busy && !root.service.refreshingLimits; focusable: true
                onClicked: root.service.refresh(root.activePane === "limits")
            }
            Ui.PanelActionButton {
                iconText: root.settingsOpen && root.hasData ? "󰅖" : "󰒓"
                tooltipText: root.settingsOpen ? "Back to dashboard" : "Connection settings"
                visible: root.hasData; focusable: true; onClicked: root.settingsOpen = !root.settingsOpen
            }
        }
        Ui.ButtonGroup {
            objectName: "paneTabs"; visible: !root.setup
            options: [{value:"limits",label:"Limits"},{value:"usage",label:"Usage"}]
            value: root.activePane; onChanged: value => root.activePane = value
        }
        Ui.PanelSeparator { Layout.fillWidth: true }
        Label {
            Layout.fillWidth: true; visible: root.service && root.service.error !== ""
            text: (root.service ? root.service.error : "") + (root.hasData ? " Showing previous results." : "")
            color: Commons.Color.urgent; wrapMode: Text.Wrap; font.pixelSize: Style.font.bodySmall
        }
        RowLayout {
            visible: !root.setup && root.filterOpen && root.activePane === "limits"; Layout.fillWidth: true; spacing: Style.space(7)
            Ui.TextField {
                id: search; objectName: "accountSearch"; Layout.fillWidth: true
                placeholderText: "Search accounts or providers…"; Accessible.name: "Filter accounts"; selectByMouse: true
                onTextChanged: root.resetScroll()
            }
        }
        RowLayout {
            visible: !root.setup && root.activePane === "limits"; Layout.fillWidth: true
            Label { text: root.accounts.length + " ACCOUNTS"; font.pixelSize: Style.font.caption; color: Qt.alpha(Commons.Color.foreground, 0.45) }
            Item { Layout.fillWidth: true }
            Label { text: "LEFT     RESETS IN"; font.pixelSize: Style.font.caption; color: Qt.alpha(Commons.Color.foreground, 0.45) }
        }
        Controls.ScrollView {
            id: scroll; objectName: "accountScroll"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: Style.space(8)
                ColumnLayout {
                    id: accountList
                    visible: !root.setup && root.activePane === "limits"; Layout.fillWidth: true; spacing: Style.space(10)
                    Repeater {
                        model: root.visibleAccounts
                        AccountRow {
                            required property var modelData
                            Layout.fillWidth: true; account: modelData
                            expanded: root.expandedId === modelData.id; privateMode: root.privateMode; now: root.now
                            quota: root.service ? (root.service.quotas[modelData.id] || null) : null
                            quotaBusy: root.service ? root.service.quotaAccountId === modelData.id : false
                            quotaQueued: root.service ? root.service.quotaQueue.indexOf(modelData.id) !== -1 : false
                            consented: root.service ? root.service.consentedAccounts[modelData.id] === true : false
                            busy: root.service ? root.service.busy || root.service.refreshingLimits : false
                            onToggled: root.toggleAccount(modelData.id)
                            onQuotaRequested: consent => root.service.checkQuota(modelData.id, consent)
                        }
                    }
                    Label {
                        Layout.fillWidth: true; visible: root.visibleAccounts.length === 0
                        text: root.accounts.length ? "No subscriptions match this filter." : "No signed-in accounts were returned by the proxy."
                        color: Qt.alpha(Commons.Color.foreground, 0.6); wrapMode: Text.Wrap; topPadding: Style.space(10)
                    }
                }
                UsagePane {
                    id: usagePane; objectName: "usagePane"
                    visible: !root.setup && root.activePane === "usage"; Layout.fillWidth: true
                    accounts: root.accounts; snapshotData: root.snapshotData
                    privateMode: root.privateMode; now: root.now
                }
                ColumnLayout {
                    visible: root.setup; Layout.fillWidth: true; spacing: Style.space(12)
                    Label { text: root.hasData ? "Connection" : "Connect your subscriptions"; font.bold: true }
                    Label {
                        Layout.fillWidth: true
                        text: "See remaining allowance and reset times across your signed-in accounts. Limits load automatically."
                        color: Qt.alpha(Commons.Color.foreground, 0.6); wrapMode: Text.Wrap
                    }
                    Ui.PanelSectionHeader { text: "SERVER URL" }
                    Ui.TextField {
                        id: address; objectName: "serverUrl"; Layout.fillWidth: true; placeholderText: "https://proxy.example.com"
                        Accessible.name: "Server URL"; selectByMouse: true; onAccepted: secret.forceActiveFocus()
                    }
                    Ui.PanelSectionHeader { text: "MANAGEMENT KEY" }
                    Ui.TextField {
                        id: secret; objectName: "managementKey"; Layout.fillWidth: true; placeholderText: "Enter management key"; password: true
                        Accessible.name: "Management key"; inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                        onAccepted: if (text.length && address.text.length) root.submit()
                    }
                    Controls.CheckBox {
                        id: remember; text: "Remember on this device"
                        font.family: Style.font.family; font.pixelSize: Style.font.body; palette.windowText: Commons.Color.foreground
                    }
                    Label {
                        Layout.fillWidth: true
                        text: remember.checked ? "Stored in a private plaintext file outside the plugin." : "Session only. The key stays in memory."
                        color: Qt.alpha(Commons.Color.foreground, 0.5); wrapMode: Text.Wrap; font.pixelSize: Style.font.bodySmall
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Ui.Button {
                            text: root.connecting ? "Connecting…" : "Connect"; selected: true; focusable: true
                            enabled: root.service && root.service.ready && !root.service.busy && address.text.trim().length && secret.text.length
                            onClicked: root.submit()
                        }
                        Item { Layout.fillWidth: true }
                        Ui.Button {
                            text: "Forget"; visible: root.service && root.service.configured
                            enabled: root.service && !root.service.busy; focusable: true; onClicked: { secret.clear(); root.service.forget() }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "HTTPS for remote servers. Local HTTP is supported on loopback. Muse quota access requires separate permission."
                        color: Qt.alpha(Commons.Color.foreground, 0.45); wrapMode: Text.Wrap; font.pixelSize: Style.font.caption
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: !root.service || !root.service.ready ? "Starting…" : root.service.refreshingLimits ? "Refreshing limits… " + root.loadedCount + "/" + root.accounts.length
                    : root.service.busy ? "Updating accounts…" : root.setup ? "PRIVATE CONNECTION" : root.activePane === "usage" ? "Updated " + Display.relative(root.snapshotData.updatedAt, root.now).toLowerCase() + " · auto-refresh 1m" : root.loadedCount + "/" + root.accounts.length + " limits loaded · auto-refresh 5m"
                color: Qt.alpha(Commons.Color.foreground, 0.45); font.pixelSize: Style.font.caption; elide: Text.ElideRight
            }
            Label { visible: !root.setup && root.activePane === "limits"; text: "Click for activity"; color: Qt.alpha(Commons.Color.foreground, 0.4); font.pixelSize: Style.font.caption }
        }
    }
    component Label: Text {
        textFormat: Text.PlainText; color: Commons.Color.foreground
        font.family: Style.font.family; font.pixelSize: Style.font.body
    }
}
