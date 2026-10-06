import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Display.js" as Display

ColumnLayout {
    id: root
    required property var account
    property var quota: null
    property bool quotaBusy: false
    property bool busy: false
    property bool expanded: false
    property bool privateMode: false
    property double now: Date.now()
    signal toggled()
    signal quotaRequested()
    spacing: Style.space(10)

    Ui.CursorSurface {
        id: header
        Layout.fillWidth: true
        implicitHeight: contents.implicitHeight + Style.space(16)
        activeFocusOnTab: true
        hasCursor: activeFocus || hover.hovered
        Accessible.role: Accessible.Button
        Accessible.name: Display.accountName(root.account, root.privateMode) + ", " + Display.providerName(root.account.provider) + ", " + Display.count(root.account.requests) + " requests. " + (root.expanded ? "Collapse" : "Expand")
        Accessible.onPressAction: root.toggled()
        Keys.onReturnPressed: root.toggled()
        Keys.onEnterPressed: root.toggled()
        Keys.onSpacePressed: root.toggled()
        HoverHandler { id: hover }
        TapHandler { onTapped: { header.forceActiveFocus(); root.toggled() } }
        ColumnLayout {
            id: contents
            anchors.fill: parent
            anchors.margins: Style.space(8)
            spacing: Style.space(10)
            RowLayout {
            Layout.fillWidth: true
                spacing: Style.space(10)
                Label {
                    text: root.account.provider === "codex" ? ">_" : root.account.provider === "claude" ? "✳" : root.account.provider === "gemini" ? "✦" : "⇄"
                    font.pixelSize: Style.font.display
                    Layout.preferredWidth: Style.space(30)
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Style.space(3)
                    Label {
                        Layout.fillWidth: true
                        text: Display.accountName(root.account, root.privateMode)
                        font.pixelSize: Style.font.title; font.bold: true
                        elide: Text.ElideMiddle
                    }
                    Label {
                        Layout.fillWidth: true
                        text: Display.providerName(root.account.provider) + ((root.quota && root.quota.plan) || root.account.plan ? " · " + ((root.quota && root.quota.plan) || root.account.plan) : "") + (root.account.kind === "api_key" ? " · API key" : "")
                        color: Qt.alpha(Color.foreground, 0.56)
                        font.pixelSize: Style.font.bodySmall; elide: Text.ElideRight
                    }
                }
                Rectangle {
                    width: Style.space(5); height: width; radius: height / 2
                    color: root.account.status === "active" ? Color.foreground : root.account.status === "error" || root.account.status === "unavailable" ? Color.urgent : Qt.alpha(Color.foreground, 0.25)
                }
                Label {
                    text: root.account.status === "unavailable" && root.account.nextRetryAt ? "Cooldown" : ({active: "Ready", disabled: "Disabled", unavailable: "Unavailable", error: "Attention", unknown: "Unknown"})[root.account.status] || "Unknown"
                    font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.62)
                }
                Label { text: root.expanded ? "−" : "+"; color: Qt.alpha(Color.foreground, 0.5) }
            }
            RowLayout {
            Layout.fillWidth: true
                Label { text: Display.count(root.account.requests); font.bold: true; font.pixelSize: Style.font.title }
                Label { text: root.account.metricsLabel === "Recorded requests" ? "requests" : "attempts"; color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption }
                Item { Layout.fillWidth: true }
                Label {
                    text: root.account.lastActivityKind === "exact" ? "Last request" : root.account.lastActivityKind === "window" ? "Last active ≈" : "Last activity"
                    color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption
                }
                Label { text: Display.activity(root.account, root.now); font.pixelSize: Style.font.bodySmall }
            }
            ActivityStrip { Layout.fillWidth: true; buckets: root.account.history || []; compact: true }
        }
    }
    ColumnLayout {
        visible: root.expanded
        Layout.fillWidth: true
        Layout.leftMargin: Style.space(8); Layout.rightMargin: Style.space(8)
        spacing: Style.space(14)
        RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(32)
            uniformCellSizes: true
            Stat { label: "SUCCESSFUL"; value: Display.count(root.account.success) }
            Stat { label: "FAILED"; value: Display.count(root.account.failed); tint: root.account.failed > 0 ? Color.urgent : Color.foreground }
            Stat { label: "TOKENS"; value: Display.compact(root.account.tokens) }
        }
        Label {
            Layout.fillWidth: true
            text: root.account.lastActivityKind === "exact"
                ? "Last request  " + new Date(root.account.lastRequestAt).toLocaleString(Qt.locale(), "ddd d MMM, hh:mm:ss")
                : root.account.lastActivityKind === "window"
                    ? "Last active in " + Display.activity(root.account, root.now) + " (server time). The server reports ten-minute windows."
                    : "No request timestamp reported. This does not mean the account has never been used."
            color: Qt.alpha(Color.foreground, 0.56); font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            visible: (root.account.history || []).length > 0
            Label { text: root.account.history && root.account.history.length ? root.account.history[0].label : ""; color: Qt.alpha(Color.foreground, 0.45); font.pixelSize: Style.font.caption }
            Item { Layout.fillWidth: true }
            Rectangle { width: 5; height: 5; color: Qt.alpha(Color.foreground, 0.55) }
            Label { text: "Success"; color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption }
            Rectangle { width: 5; height: 5; color: Color.urgent }
            Label { text: "Failed"; color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption }
        }
        Label {
            visible: !!root.account.nextRetryAt
            text: "Retry available  " + (root.account.nextRetryAt ? new Date(root.account.nextRetryAt).toLocaleString(Qt.locale(), "ddd hh:mm") : "")
            color: Color.urgent; font.pixelSize: Style.font.bodySmall
        }
        Ui.PanelSeparator { Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            Ui.PanelSectionHeader { text: "USAGE LIMITS"; Layout.fillWidth: true }
            Ui.Button {
                text: root.quotaBusy ? "Checking…" : root.quota ? "Refresh limits" : "Check limits"
                visible: root.account.quotaSupported === true
                enabled: !root.busy; focusable: true
                onClicked: root.quotaRequested()
            }
        }
        QuotaWindows { Layout.fillWidth: true; windows: root.quota ? (root.quota.windows || []) : []; now: root.now }
        Label {
            visible: !root.quota || !!root.quota.error || (root.quota.windows || []).length === 0
            Layout.fillWidth: true
            text: root.quota && root.quota.error ? root.quota.error
                : !root.account.quotaSupported ? "This provider does not expose supported account limits."
                : root.quota ? "No usage windows were reported for this account."
                : "Check the remaining allowance for this account."
            wrapMode: Text.Wrap
            color: root.quota && root.quota.error ? Color.urgent : Qt.alpha(Color.foreground, 0.55)
            font.pixelSize: Style.font.bodySmall
        }
        Label {
            visible: !!(root.quota && root.quota.updatedAt)
            text: root.quota && root.quota.updatedAt ? "Limits checked " + Display.relative(root.quota.updatedAt, root.now).toLowerCase() : ""
            color: Qt.alpha(Color.foreground, 0.45); font.pixelSize: Style.font.caption
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: (root.account.models || []).length > 0
            spacing: Style.space(8)
            Ui.PanelSeparator { Layout.fillWidth: true }
            Ui.PanelSectionHeader { text: "RECORDED MODELS" }
            Repeater {
                model: root.account.models || []
                RowLayout {
            Layout.fillWidth: true
                    required property var modelData
                    Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: Style.font.bodySmall }
                    Label { text: Display.count(modelData.requests); font.pixelSize: Style.font.bodySmall }
                    Label { text: Display.compact(modelData.tokens) + " tokens"; color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption }
                }
            }
        }
        Label {
            Layout.fillWidth: true
            text: root.account.metricsLabel === "Recorded requests" ? "Counts and tokens from recorded requests matched to this account."
                : "Upstream attempts include retries. Token totals need account-attributed request history."
            wrapMode: Text.Wrap; color: Qt.alpha(Color.foreground, 0.45); font.pixelSize: Style.font.caption
        }
        Item { implicitHeight: Style.space(2) }
    }
    Ui.PanelSeparator { Layout.fillWidth: true }
    component Label: Text {
        textFormat: Text.PlainText; color: Color.foreground
        font.family: Style.font.family; font.pixelSize: Style.font.body
    }
    component Stat: ColumnLayout {
        property string label: ""
        property string value: ""
        property color tint: Color.foreground
        Layout.fillWidth: true
        spacing: Style.space(5)
        Label { text: label; color: Qt.alpha(Color.foreground, 0.5); font.pixelSize: Style.font.caption }
        Label { text: value; color: tint; font.pixelSize: Style.font.title; font.bold: true }
    }
}
