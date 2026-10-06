import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Display.js" as Display

ColumnLayout {
    id: root
    required property var accounts
    required property var snapshotData
    property bool privateMode: false
    property double now: Date.now()
    property string expandedProvider: ""
    property string metric: "requests"
    readonly property var groups: Display.usageGroups(accounts)
    readonly property var summary: Display.usageSummary(accounts)
    readonly property bool hasTokens: snapshotData.tokenUsageAvailable === true || snapshotData.usageAvailable === true
    readonly property real maximum: Display.usageMaximum(groups, metric)
    spacing: Style.space(12)
    onAccountsChanged: if (!groups.some(g => g.provider === expandedProvider)) expandedProvider = ""

    RowLayout {
        Layout.fillWidth: true; spacing: Style.space(12)
        ColumnLayout {
            Layout.fillWidth: true; spacing: Style.space(3)
            Label { text: root.summary.metricsLabel.toUpperCase(); font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.5) }
            Label { text: Display.metricText(root.summary.requests, true); font.pixelSize: Style.font.title; font.bold: true }
        }
        ColumnLayout {
            spacing: Style.space(3)
            Label { text: "REPORTED TOKENS"; font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.5) }
            Label { text: Display.metricText(root.summary.tokenMetrics.total); font.pixelSize: Style.font.title; font.bold: true }
        }
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.55)
        text: root.snapshotData.usageSource === "collector"
            ? "Collected since " + Display.shortDate(root.snapshotData.usageSince) + " · " + Display.count(root.summary.usageRecords) + " upstream attempts." + (root.snapshotData.usagePartial ? " Collection is incomplete." : "")
            : root.snapshotData.usageAvailable
            ? "Token history · " + Display.count(root.summary.usageRecords) + " matched records. Counters may cover a different period."
            : "Token history unavailable. Enable the companion collector on your proxy to start recording token metrics."
    }
    Label {
        Layout.fillWidth: true; visible: !!root.snapshotData.collectorNotice
        text: root.snapshotData.collectorNotice || ""; wrapMode: Text.Wrap
        font.pixelSize: Style.font.caption; color: Color.urgent
    }
    RowLayout {
        Layout.fillWidth: true
        Label { text: "BY PROVIDER"; font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.45) }
        Item { Layout.fillWidth: true }
        Ui.Dropdown {
            objectName: "usageMetric"
            Layout.preferredWidth: Style.space(152); showLabel: false; value: root.metric
            options: [{value:"requests",label:"Requests"},{value:"total",label:"Total tokens"},
                {value:"input",label:"Input tokens"},{value:"output",label:"Output tokens"},
                {value:"cached",label:"Cached tokens"},{value:"reasoning",label:"Reasoning tokens"},
                {value:"cacheRead",label:"Cache read"},{value:"cacheWrite",label:"Cache write"}]
            onChanged: value => root.metric = value
        }
    }
    Repeater {
        model: root.groups
        ColumnLayout {
            id: group
            required property var modelData
            readonly property bool expanded: root.expandedProvider === modelData.provider
            readonly property var selectedMetric: Display.usageMetric(modelData, root.metric)
            Layout.fillWidth: true; spacing: Style.space(7)
            Ui.CursorSurface {
                id: header
                objectName: "usageProvider_" + group.modelData.provider
                Layout.fillWidth: true; implicitHeight: identity.implicitHeight + Style.space(6)
                activeFocusOnTab: true; hasCursor: activeFocus || hover.hovered
                function toggle() { root.expandedProvider = group.expanded ? "" : group.modelData.provider }
                Accessible.role: Accessible.Button
                Accessible.name: Display.providerName(group.modelData.provider) + (group.expanded ? ", hide accounts" : ", show accounts")
                Accessible.onPressAction: toggle()
                Keys.onReturnPressed: toggle()
                Keys.onEnterPressed: toggle()
                Keys.onSpacePressed: toggle()
                HoverHandler { id: hover }
                TapHandler { onTapped: { header.forceActiveFocus(); header.toggle() } }
                RowLayout {
                    id: identity
                    anchors.fill: parent; anchors.topMargin: Style.space(3); anchors.bottomMargin: Style.space(3)
                    spacing: Style.space(8)
                    ProviderLogo { provider: group.modelData.provider }
                    Label { text: Display.providerName(group.modelData.provider); font.bold: true }
                    Label { text: group.modelData.accounts.length + " acct"; color: Qt.alpha(Color.foreground, 0.45); font.pixelSize: Style.font.caption }
                    Item { Layout.fillWidth: true }
                    Label { text: Display.metricText(group.selectedMetric); font.bold: true }
                    Label { text: group.expanded ? "−" : "+"; color: Qt.alpha(Color.foreground, 0.4) }
                }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: Style.space(3); color: Qt.alpha(Color.foreground, 0.1)
                Rectangle { height: parent.height; width: parent.width * Display.metricRatio(group.selectedMetric, root.maximum); color: Color.accent }
            }
            UsageMetrics { Layout.fillWidth: true; visible: root.hasTokens; summary: group.modelData }
            Repeater {
                model: group.expanded ? group.modelData.accounts : []
                ColumnLayout {
                    id: accountRow
                    required property var modelData
                    readonly property var totals: Display.usageSummary([modelData])
                    Layout.fillWidth: true; Layout.leftMargin: Style.space(10); spacing: Style.space(6)
                    Ui.PanelSeparator { Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: Display.accountName(accountRow.modelData, root.privateMode); elide: Text.ElideMiddle; font.bold: true; font.pixelSize: Style.font.bodySmall }
                        Label { text: Display.metricText(Display.usageMetric(accountRow.totals, root.metric)); font.pixelSize: Style.font.bodySmall }
                    }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.55)
                        text: Display.metricText(accountRow.totals.requests, true) + (accountRow.totals.metricsLabel === "Recorded requests" ? " requests" : " attempts")
                            + " · " + Display.metricText(accountRow.totals.failed, true) + " failed · " + Display.metricText(accountRow.totals.tokenMetrics.total) + " tokens"
                    }
                    UsageMetrics { Layout.fillWidth: true; visible: root.hasTokens; summary: accountRow.totals; detailed: true }
                    Label {
                        text: "RECENT SERVER ACTIVITY · 10M BUCKETS"
                        font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.4)
                    }
                    ActivityStrip { Layout.fillWidth: true; buckets: accountRow.modelData.history || []; compact: true }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.45)
                        text: accountRow.modelData.lastActivityKind === "exact" ? "Last request " + Display.relative(accountRow.modelData.lastRequestAt, root.now)
                            : accountRow.modelData.lastActivityKind === "window" ? "Last active ≈ " + Display.activity(accountRow.modelData, root.now) + " · server time" : "Last request not reported"
                    }
                }
            }
            Ui.PanelSeparator { Layout.fillWidth: true }
        }
    }
    Label { Layout.fillWidth: true; visible: root.groups.length === 0; text: "No accounts match this filter."; color: Qt.alpha(Color.foreground, 0.5) }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Style.font.caption; color: Qt.alpha(Color.foreground, 0.45)
        text: "Bars compare the selected metric across shown providers. * Partial data; — not reported."
            + (root.hasTokens ? "\n" + (root.snapshotData.tokenSemantics || "Reported token fields are not additive.") : "")
            + (root.snapshotData.usageUnattributedRecords ? "\n" + Display.count(root.snapshotData.usageUnattributedRecords) + " unmatched records excluded." : "")
            + (root.snapshotData.usageSource === "collector" ? "\nCollector totals persist across restarts. Attempts include retries and fallbacks." : "\nServer snapshot · counters can reset on restart. Attempts include retries and fallbacks.")
    }
    component Label: Text {
        textFormat: Text.PlainText; color: Color.foreground
        font.family: Style.font.family; font.pixelSize: Style.font.body
    }
}
