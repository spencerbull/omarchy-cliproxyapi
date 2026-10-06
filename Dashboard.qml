import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui

FocusScope {
    id: root
    required property var service
    signal dismiss()
    property int page: 0
    readonly property var snapshotData: service && service.snapshot ? service.snapshot : ({})
    readonly property bool hasData: !!(service && service.snapshot)
    readonly property bool setup: page === 2 || !hasData
    readonly property color ink: Color.foreground
    readonly property color muted: Qt.alpha(ink, 0.58)
    readonly property color line: Qt.alpha(ink, 0.12)
    readonly property color accent: Color.accent
    readonly property var connections: snapshotData.connections || []
    readonly property var models: snapshotData.models || []
    readonly property var clients: snapshotData.clients || []
    readonly property var history: snapshotData.history || []
    readonly property real peak: Math.max(1, ...history.map(row => row.requests))

    function number(value) {
        if (value === null || value === undefined) return "—"
        return Number(value).toLocaleString(Qt.locale(), "f", 0)
    }
    function compact(value) {
        if (value === null || value === undefined) return "—"
        if (value >= 1000000) return (value / 1000000).toFixed(1) + "M"
        if (value >= 1000) return (value / 1000).toFixed(1) + "k"
        return String(value)
    }
    function submit() {
        if (service && service.connectTo(address.text.trim(), secret.text, remember.checked)) {
            secret.clear()
            page = 0
        }
    }
    Keys.onEscapePressed: dismiss()
    Connections {
        target: root.service
        function onUrlChanged() { address.text = root.service.url }
        function onRememberChanged() { remember.checked = root.service.remember }
    }
    Component.onCompleted: {
        if (service) {
            address.text = service.url
            remember.checked = service.remember
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(18)

        RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(12)
            Rectangle {
                width: Style.space(44); height: width
                radius: Style.space(12)
                color: Qt.alpha(root.accent, 0.14)
                Text {
                    anchors.centerIn: parent
                    text: "⌘"; color: root.accent
                    font.family: Style.font.family
                    font.pixelSize: Style.font.display
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(3)
                Label { text: "CLIProxyAPI"; font.pixelSize: Style.font.heading; font.bold: true }
                Label { text: "Your agent traffic, at a glance"; color: root.muted; font.pixelSize: Style.font.bodySmall }
            }
            Rectangle {
                implicitWidth: statusLabel.implicitWidth + Style.space(18)
                implicitHeight: Style.space(25)
                radius: height / 2
                color: Qt.alpha(root.service && root.service.error ? Color.urgent : root.accent, 0.12)
                Label {
                    id: statusLabel
                    anchors.centerIn: parent
                    text: !root.service || !root.service.ready ? "STARTING" : root.service.busy ? "SYNCING" : root.service.error ? "OFFLINE" : root.hasData ? "CONNECTED" : "SETUP"
                    color: root.service && root.service.error ? Color.urgent : root.accent
                    font.pixelSize: Style.font.caption
                    font.letterSpacing: 1
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(4)
            Repeater {
                model: ["Overview", "Connections", "Settings"]
                Ui.Button {
                    required property string modelData
                    required property int index
                    text: modelData
                    selected: root.setup ? index === 2 : root.page === index
                    enabled: root.hasData || index === 2
                    focusable: true
                    onClicked: root.page = index
                }
            }
            Item { Layout.fillWidth: true }
            Ui.Button {
                text: "↻ Refresh"
                visible: root.hasData
                enabled: root.service && !root.service.busy
                focusable: true
                onClicked: root.service.refresh()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: errorLabel.implicitHeight + Style.space(22)
            radius: Style.space(8)
            color: Qt.alpha(Color.urgent, 0.10)
            visible: root.service && root.service.error !== ""
            Label {
                id: errorLabel
                anchors.fill: parent; anchors.margins: Style.space(11)
                text: (root.service ? root.service.error : "") + (root.hasData ? " Showing the last successful snapshot." : "")
                wrapMode: Text.Wrap
                color: Color.urgent
            }
        }

        Controls.ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

            ColumnLayout {
                width: scroll.availableWidth
                spacing: Style.space(18)

                ColumnLayout {
                    visible: root.setup
                    Layout.fillWidth: true
                    spacing: Style.space(14)
                    Label { text: root.hasData ? "Connection settings" : "Connect your proxy"; font.pixelSize: Style.font.display; font.bold: true }
                    Label {
                        Layout.fillWidth: true
                        text: "Enter your server URL and management key to see usage and the provider connections behind your agents."
                        color: root.muted; wrapMode: Text.Wrap
                    }
                    Label { text: "SERVER URL"; color: root.muted; font.pixelSize: Style.font.caption; font.letterSpacing: 1 }
                    Ui.TextField {
                        id: address
                        Layout.fillWidth: true
                        placeholderText: "https://proxy.example.com"
                        selectByMouse: true
                        inputMethodHints: Qt.ImhUrlCharactersOnly
                        Accessible.name: "Server URL"
                        onAccepted: secret.forceActiveFocus()
                    }
                    Label { text: "MANAGEMENT KEY"; color: root.muted; font.pixelSize: Style.font.caption; font.letterSpacing: 1 }
                    Ui.TextField {
                        id: secret
                        Layout.fillWidth: true
                        placeholderText: "Enter management key"
                        password: true
                        selectByMouse: true
                        inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                        Accessible.name: "Management key"
                        onAccepted: if (text.length && address.text.length) root.submit()
                    }
                    Controls.CheckBox {
                        id: remember
                        text: "Remember on this device"
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                        palette.windowText: root.ink
                    }
                    Label {
                        Layout.fillWidth: true
                        text: remember.checked
                            ? "Saved outside the plugin in a private file readable only by your user. The key is stored as plaintext."
                            : "Session only. Your key stays in memory and is cleared when the shell restarts."
                        color: root.muted; wrapMode: Text.Wrap
                        font.pixelSize: Style.font.bodySmall
                    }
                    RowLayout {
                        Ui.Button {
                            text: root.service && root.service.busy ? "Connecting…" : "Connect"
                            selected: true; bordered: true; focusable: true
                            enabled: root.service && root.service.ready && !root.service.busy && address.text.trim().length > 0 && secret.text.length > 0
                            onClicked: root.submit()
                        }
                        Ui.Button {
                            text: "Forget connection"
                            visible: root.service && root.service.configured
                            enabled: root.service && !root.service.busy
                            focusable: true
                            onClicked: { secret.clear(); root.service.forget() }
                        }
                    }
                    Rule {}
                    Label {
                        Layout.fillWidth: true
                        text: "Read-only access · HTTPS for remote servers\nLocal HTTP is supported on localhost and loopback addresses."
                        color: root.muted; wrapMode: Text.Wrap
                        font.pixelSize: Style.font.bodySmall
                    }
                }

                ColumnLayout {
                    visible: !root.setup && root.page === 0
                    Layout.fillWidth: true
                    spacing: Style.space(18)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(10)
                        Metric { title: root.snapshotData.usageAvailable ? "REQUESTS" : "UPSTREAM ATTEMPTS"; value: root.compact(root.snapshotData.totalRequests); detail: "Since server start" }
                        Metric { title: "TOKENS"; value: root.compact(root.snapshotData.totalTokens); detail: root.snapshotData.usageAvailable ? "Reported by proxy" : "Not exposed by server" }
                        Metric { title: "SUCCESS"; value: root.snapshotData.totalRequests > 0 ? (100 * root.snapshotData.success / root.snapshotData.totalRequests).toFixed(1) + "%" : "—"; detail: root.number(root.snapshotData.failed) + " failed" }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(12)
                        SectionTitle { title: "Activity"; subtitle: root.snapshotData.usageAvailable ? "Daily requests" : "Recent upstream attempts · server time" }
                        Row {
                            Layout.fillWidth: true
                            height: Style.space(82)
                            spacing: Style.space(3)
                            visible: root.history.length > 0
                            Repeater {
                                model: root.history
                                Item {
                                    required property var modelData
                                    width: Math.max(1, (parent.width - (root.history.length - 1) * parent.spacing) / Math.max(1, root.history.length))
                                    height: parent.height
                                    Rectangle {
                                        anchors.bottom: parent.bottom
                                        width: parent.width
                                        height: Math.max(2, parent.height * modelData.requests / root.peak)
                                        radius: Math.min(3, width / 3)
                                        color: modelData.requests > 0 ? root.accent : root.line
                                        opacity: 0.75
                                    }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true
                                        Controls.ToolTip.visible: containsMouse
                                        Controls.ToolTip.text: modelData.label + " · " + root.number(modelData.requests)
                                    }
                                }
                            }
                        }
                        RowLayout {
                            visible: root.history.length > 0
                            Label { text: root.history.length ? root.history[0].label : ""; color: root.muted; font.pixelSize: Style.font.caption }
                            Item { Layout.fillWidth: true }
                            Label { text: root.history.length ? root.history[root.history.length - 1].label : ""; color: root.muted; font.pixelSize: Style.font.caption }
                        }
                        Label { visible: root.history.length === 0; text: "No activity history reported yet."; color: root.muted }
                    }
                    Rule {}
                    SectionTitle { title: "Model usage"; subtitle: root.models.length + " models" }
                    Repeater {
                        model: root.models
                        DataRow {
                            required property var modelData
                            title: modelData.name
                            subtitle: root.number(modelData.tokens) + " tokens"
                            value: root.number(modelData.requests)
                            detail: "requests"
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.models.length === 0
                        text: root.snapshotData.usageAvailable ? "Model activity will appear after your agents send requests." : "This server version does not expose model or token totals through a read-only API."
                        wrapMode: Text.Wrap; color: root.muted
                    }
                    Rule {}
                    SectionTitle { title: "Client usage"; subtitle: "One client per API key" }
                    Repeater {
                        model: root.clients
                        DataRow {
                            required property var modelData
                            title: modelData.name
                            subtitle: root.number(modelData.tokens) + " tokens"
                            value: root.number(modelData.requests)
                            detail: "requests"
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Clients are anonymous API-key groups. Use a separate key per agent to distinguish its traffic. Shared keys cannot identify individual agents."
                        wrapMode: Text.Wrap; color: root.muted; font.pixelSize: Style.font.bodySmall
                    }
                }

                ColumnLayout {
                    visible: !root.setup && root.page === 1
                    Layout.fillWidth: true
                    spacing: Style.space(14)
                    SectionTitle { title: "Provider connections"; subtitle: root.connections.length + " configured" }
                    Label {
                        Layout.fillWidth: true
                        text: "Credentials configured in your proxy. Account names, emails, and keys stay hidden."
                        color: root.muted; wrapMode: Text.Wrap
                    }
                    Repeater {
                        model: root.connections
                        Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: connectionContent.implicitHeight + Style.space(24)
                            radius: Style.space(9)
                            color: Qt.alpha(root.ink, 0.035)
                            border.color: root.line
                            ColumnLayout {
                                id: connectionContent
                                anchors.fill: parent; anchors.margins: Style.space(12)
                                spacing: Style.space(9)
                                RowLayout {
                                    Label { text: modelData.provider; color: root.accent; font.bold: true }
                                    Label { text: " / " + modelData.name; color: root.muted; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: modelData.status; color: modelData.status === "active" ? root.accent : root.muted; font.pixelSize: Style.font.bodySmall }
                                }
                                RowLayout {
                                    Label { text: root.number(modelData.success) + " successful"; font.pixelSize: Style.font.bodySmall }
                                    Item { Layout.fillWidth: true }
                                    Label { text: root.number(modelData.failed) + " failed"; color: modelData.failed > 0 ? Color.urgent : root.muted; font.pixelSize: Style.font.bodySmall }
                                }
                            }
                        }
                    }
                    Label { visible: root.connections.length === 0; text: "No provider connections were reported."; color: root.muted }
                }

                Repeater {
                    model: !root.setup ? (root.snapshotData.notices || []) : []
                    Label {
                        required property string modelData
                        Layout.fillWidth: true
                        text: modelData
                        color: root.muted; wrapMode: Text.Wrap
                        font.pixelSize: Style.font.bodySmall
                    }
                }
            }
        }

        Rule {}
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: root.hasData ? root.snapshotData.url : "No credentials in shell settings or Git"
                color: root.muted; elide: Text.ElideMiddle
                font.pixelSize: Style.font.caption
            }
            Label {
                text: root.hasData && root.snapshotData.updatedAt ? "Updated " + new Date(root.snapshotData.updatedAt).toLocaleTimeString(Qt.locale(), "hh:mm:ss") : "READ ONLY"
                color: root.muted; font.pixelSize: Style.font.caption
            }
        }
    }

    component Label: Text {
        textFormat: Text.PlainText
        color: root.ink
        font.family: Style.font.family
        font.pixelSize: Style.font.body
    }
    component Rule: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: root.line
    }
    component SectionTitle: RowLayout {
        property string title: ""
        property string subtitle: ""
        Layout.fillWidth: true
        Label { text: title; font.bold: true; font.pixelSize: Style.font.title; Layout.fillWidth: true }
        Label { text: subtitle; color: root.muted; font.pixelSize: Style.font.caption }
    }
    component Metric: Rectangle {
        property string title: ""
        property string value: ""
        property string detail: ""
        Layout.fillWidth: true
        implicitHeight: Style.space(109)
        radius: Style.space(9)
        color: Qt.alpha(root.accent, 0.055)
        border.color: root.line
        Column {
            anchors.fill: parent; anchors.margins: Style.space(12)
            spacing: Style.space(8)
            Label { width: parent.width; text: title; color: root.muted; font.pixelSize: Style.font.caption; elide: Text.ElideRight }
            Label { text: value; font.pixelSize: Style.font.displayLarge; font.bold: true }
            Label { width: parent.width; text: detail; color: root.muted; font.pixelSize: Style.font.caption; elide: Text.ElideRight }
        }
    }
    component DataRow: RowLayout {
        property string title: ""
        property string subtitle: ""
        property string value: ""
        property string detail: ""
        Layout.fillWidth: true
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(3)
            Label { text: title; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: subtitle; color: root.muted; font.pixelSize: Style.font.caption }
        }
        Label { text: value; font.bold: true }
        Label { text: detail; color: root.muted; font.pixelSize: Style.font.caption }
    }
}
