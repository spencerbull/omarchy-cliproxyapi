import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Display.js" as Display

FocusScope {
    id: root
    required property var service
    signal dismiss()
    property bool settingsOpen: false
    property bool privateMode: false
    property bool connecting: false
    property string selectedProvider: "all"
    property string sortMode: "recent"
    property string expandedId: ""
    property double now: Date.now()
    readonly property var snapshotData: service && service.snapshot ? service.snapshot : ({})
    readonly property bool hasData: !!(service && service.snapshot)
    readonly property bool setup: settingsOpen || !hasData
    readonly property var accounts: snapshotData.accounts || []
    readonly property var providerOptions: Display.providers(accounts)
    readonly property var visibleAccounts: Display.filtered(accounts, selectedProvider, search.text, sortMode, privateMode)
    readonly property var totals: Display.summary(visibleAccounts)

    function submit() {
        if (service && service.connectTo(address.text.trim(), secret.text, remember.checked)) {
            secret.clear()
            connecting = true
        }
    }
    function toggleAccount(id) { expandedId = expandedId === id ? "" : id }
    function resetScroll() { if (scroll.contentItem) scroll.contentItem.contentY = 0 }
    onPrivateModeChanged: if (privateMode) search.clear()
    onSelectedProviderChanged: { expandedId = ""; resetScroll() }
    onSetupChanged: {
        resetScroll()
        if (setup) Qt.callLater(function() { address.forceActiveFocus() })
        else secret.clear()
    }
    Keys.onEscapePressed: {
        if (settingsOpen && hasData) settingsOpen = false
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
        anchors.fill: parent
        spacing: Style.space(14)
        RowLayout {
            spacing: Style.space(10)
            Ui.PanelHero {
                Layout.fillWidth: true
                title: "CLIProxyAPI"
                meta: root.setup ? "ACCOUNT MONITOR" : root.accounts.length + " ACCOUNTS · " + Math.max(0, root.providerOptions.length - 1) + " PROVIDERS"
                iconComponent: Component { Label { text: "󰒋"; font.pixelSize: Style.font.displayLarge } }
            }
            Ui.PanelActionButton {
                iconText: root.privateMode ? "󰈉" : "󰈈"
                tooltipText: root.privateMode ? "Show account names" : "Hide account names"
                visible: !root.setup; focusable: true
                onClicked: root.privateMode = !root.privateMode
            }
            Ui.PanelActionButton {
                iconText: "󰑐"; tooltipText: "Refresh account activity"
                visible: !root.setup; enabled: root.service && !root.service.busy; focusable: true
                onClicked: root.service.refresh()
            }
            Ui.PanelActionButton {
                iconText: root.settingsOpen && root.hasData ? "󰅖" : "󰒓"
                tooltipText: root.settingsOpen ? "Back to accounts" : "Connection settings"
                visible: root.hasData; focusable: true
                onClicked: root.settingsOpen = !root.settingsOpen
            }
        }
        Ui.PanelSeparator { Layout.fillWidth: true }
        Label {
            Layout.fillWidth: true
            visible: root.service && root.service.error !== ""
            text: (root.service ? root.service.error : "") + (root.hasData ? " Showing the last snapshot." : "")
            color: Color.urgent; wrapMode: Text.Wrap
        }
        ColumnLayout {
            visible: !root.setup
            Layout.fillWidth: true
            spacing: Style.space(14)
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true; spacing: Style.space(3)
                    Label { text: Display.count(root.totals.requests); font.pixelSize: Style.font.displayLarge; font.bold: true }
                    Label { text: "RECORDED ATTEMPTS" + (root.totals.partial ? " · PARTIAL" : ""); font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.5) }
                }
                Item { Layout.fillWidth: true }
                ColumnLayout {
                    Layout.alignment: Qt.AlignBottom; spacing: Style.space(6)
                    Label { text: root.totals.ready + " ready / " + root.visibleAccounts.length; font.pixelSize: Style.font.bodySmall }
                    Label { text: Display.count(root.totals.failed) + " failed attempts"; color: root.totals.failed > 0 ? Color.urgent : Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption }
                }
            }
            Flow {
                Layout.fillWidth: true; spacing: Style.space(3)
                Repeater {
                    model: root.providerOptions
                    Ui.Button {
                        required property var modelData
                        text: modelData.label + " " + modelData.count
                        selected: root.selectedProvider === modelData.value; focusable: true
                        onClicked: root.selectedProvider = modelData.value
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Ui.TextField {
                    id: search; objectName: "accountSearch"
                    Layout.fillWidth: true; placeholderText: "Filter accounts…"
                    Accessible.name: "Filter accounts"; selectByMouse: true
                    onTextChanged: root.resetScroll()
                }
                Ui.Dropdown {
                    Layout.preferredWidth: Style.space(125); showLabel: false
                    value: root.sortMode
                    options: [{value: "recent", label: "Recent first"}, {value: "requests", label: "Most requests"}, {value: "provider", label: "By provider"}]
                    onChanged: value => { root.sortMode = value; root.resetScroll() }
                }
            }
        }
        Controls.ScrollView {
            id: scroll; objectName: "accountScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: Style.space(10)
                ColumnLayout {
                    visible: !root.setup; Layout.fillWidth: true; spacing: Style.space(6)
                    Repeater {
                        model: root.visibleAccounts
                        AccountRow {
                            required property var modelData
                            Layout.fillWidth: true; account: modelData
                            expanded: root.expandedId === modelData.id
                            privateMode: root.privateMode; now: root.now
                            quota: root.service && root.service.quotas ? (root.service.quotas[modelData.id] || null) : null
                            quotaBusy: root.service ? root.service.quotaAccountId === modelData.id : false
                            busy: root.service ? root.service.busy : false
                            onToggled: root.toggleAccount(modelData.id)
                            onQuotaRequested: root.service.checkQuota(modelData.id)
                        }
                    }
                    Label {
                        Layout.fillWidth: true; visible: root.visibleAccounts.length === 0
                        text: root.accounts.length ? "No accounts match this filter." : "No accounts were returned by the proxy. Signed-in accounts will appear here."
                        color: Qt.alpha(Color.foreground, 0.6); wrapMode: Text.Wrap
                        topPadding: Style.space(20); bottomPadding: Style.space(20)
                    }
                }
                ColumnLayout {
                    visible: root.setup; Layout.fillWidth: true; spacing: Style.space(14)
                    Label { text: root.hasData ? "Connection" : "Connect your accounts"; font.pixelSize: Style.font.heading; font.bold: true }
                    Label {
                        Layout.fillWidth: true
                        text: "Connect to CLIProxyAPI to see which accounts are being used, when they were last active, and how much allowance remains."
                        color: Qt.alpha(Color.foreground, 0.6); wrapMode: Text.Wrap
                    }
                    Ui.PanelSectionHeader { text: "SERVER URL" }
                    Ui.TextField {
                        id: address; objectName: "serverUrl"
                        Layout.fillWidth: true; placeholderText: "https://proxy.example.com"
                        Accessible.name: "Server URL"; selectByMouse: true
                        onAccepted: secret.forceActiveFocus()
                    }
                    Ui.PanelSectionHeader { text: "MANAGEMENT KEY" }
                    Ui.TextField {
                        id: secret; objectName: "managementKey"
                        Layout.fillWidth: true; placeholderText: "Enter management key"; password: true
                        Accessible.name: "Management key"
                        inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                        onAccepted: if (text.length && address.text.length) root.submit()
                    }
                    Controls.CheckBox {
                        id: remember; text: "Remember on this device"
                        font.family: Style.font.family; font.pixelSize: Style.font.body; palette.windowText: Color.foreground
                    }
                    Label {
                        Layout.fillWidth: true
                        text: remember.checked ? "Saved in a private local file outside the plugin. The key is stored as plaintext." : "Session only. Your key stays in memory until the shell restarts."
                        color: Qt.alpha(Color.foreground, 0.5); wrapMode: Text.Wrap; font.pixelSize: Style.font.bodySmall
                    }
                    RowLayout {
                        Ui.Button {
                            text: root.connecting ? "Connecting…" : "Connect"; selected: true; focusable: true
                            enabled: root.service && root.service.ready && !root.service.busy && address.text.trim().length && secret.text.length
                            onClicked: root.submit()
                        }
                        Item { Layout.fillWidth: true }
                        Ui.Button {
                            text: "Forget connection"; visible: root.service && root.service.configured
                            enabled: root.service && !root.service.busy; focusable: true
                            onClicked: { secret.clear(); root.service.forget() }
                        }
                    }
                    Ui.PanelSeparator { Layout.fillWidth: true }
                    Label {
                        Layout.fillWidth: true
                        text: "Remote connections use HTTPS. Local HTTP is supported on loopback. Account limits are checked only when you ask."
                        color: Qt.alpha(Color.foreground, 0.5); wrapMode: Text.Wrap; font.pixelSize: Style.font.bodySmall
                    }
                }
            }
        }
        Ui.PanelSeparator { Layout.fillWidth: true }
        RowLayout {
            Label {
                Layout.fillWidth: true
                text: !root.service || !root.service.ready ? "Starting…" : root.service.busy ? "Updating…" : root.snapshotData.updatedAt ? "Updated " + new Date(root.snapshotData.updatedAt).toLocaleTimeString(Qt.locale(), "hh:mm:ss") : "URL + management key"
                color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption
            }
            Label { text: root.setup ? "PRIVATE CONNECTION" : "Click an account for details"; color: Qt.alpha(Color.foreground, 0.4); font.pixelSize: Style.font.caption }
        }
    }
    component Label: Text {
        textFormat: Text.PlainText; color: Color.foreground
        font.family: Style.font.family; font.pixelSize: Style.font.body
    }
}
