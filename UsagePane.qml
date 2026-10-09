import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Display.js" as Display

ColumnLayout {
    id: root
    required property var accounts
    required property var snapshotData
    property bool privateMode: false
    property double now: Date.now()
    property string period: "7"
    property string breakdown: "model"
    property string expandedGroup: ""
    property bool detailsOpen: false
    readonly property var history: snapshotData.usageHistory || ({available: false})
    readonly property var view: Display.tokenWindow(history, Number(period), breakdown, accounts, privateMode)
    readonly property var totals: view.available ? view.summary : Display.usageSummary(accounts)
    readonly property var groups: view.available ? view.groups : Display.tokenGroups(
        accounts.filter(a => (a.usageRecords || 0) > 0).map(a => Object.assign({}, a, {accountId: a.id})),
        "provider", accounts, privateMode, snapshotData.usagePartial === true)
    readonly property real maximum: Math.max.apply(null, [0].concat(view.series.map(point => point.metric.value || 0)))
    spacing: Style.space(12)
    onPeriodChanged: expandedGroup = ""
    onBreakdownChanged: expandedGroup = ""

    RowLayout {
        Layout.fillWidth: true
        Label { text: "TOKEN USAGE"; muted: true; font.pixelSize: Style.font.caption; Layout.fillWidth: true }
        Ui.ButtonGroup {
            objectName: "usagePeriod"; value: root.period; enabled: root.view.available; opacity: enabled ? 1 : 0.35
            options: [{value:"1",label:"1D",tooltip:"Today (UTC)"}, {value:"7",label:"7D",tooltip:"7 UTC days including today"}, {value:"30",label:"30D",tooltip:"30 UTC days including today"}]
            onChanged: value => root.period = value
        }
    }
    ColumnLayout {
        Layout.fillWidth: true; spacing: Style.space(4)
        RowLayout {
            Layout.fillWidth: true; spacing: Style.space(8)
            Label {
                text: Display.metricText(root.totals.tokenMetrics.total)
                font.pixelSize: Style.font.title * 1.7; font.bold: true
                Accessible.name: "Total tokens: " + Display.metricText(root.totals.tokenMetrics.total, true)
                HoverHandler { id: totalHover }
                Controls.ToolTip { visible: totalHover.hovered; text: Display.metricText(root.totals.tokenMetrics.total, true) + " tokens" }
            }
            Label { text: "tokens"; muted: true; Layout.alignment: Qt.AlignBottom; bottomPadding: Style.space(5) }
            Item { Layout.fillWidth: true }
            Ui.Button {
                text: root.detailsOpen ? "Less" : "Details"; focusable: true
                tooltipText: "Show reasoning, cache read, and cache write"
                onClicked: root.detailsOpen = !root.detailsOpen
            }
        }
        Label {
            Layout.fillWidth: true; font.pixelSize: Style.font.caption; muted: true; wrapMode: Text.Wrap
            text: root.view.available
                ? "All models · " + (root.period === "1" ? "Today" : Display.utcDate(root.view.from) + " – " + Display.utcDate(root.view.to)) + " · UTC"
                : (root.snapshotData.usageSource === "collector" ? "Lifetime totals · all linked accounts" : "Server snapshot · all linked accounts")
        }
    }
    UsageMetrics { Layout.fillWidth: true; summary: root.totals; detailed: root.detailsOpen }
    ColumnLayout {
        visible: root.view.available && root.period !== "1"; Layout.fillWidth: true; spacing: Style.space(4)
        Row {
            Layout.fillWidth: true; height: Style.space(48); spacing: Style.space(3)
            Repeater {
                model: root.view.series
                Item {
                    id: dayBar
                    required property var modelData
                    width: Math.max(1, (parent.width - parent.spacing * (root.view.series.length - 1)) / Math.max(1, root.view.series.length))
                    height: parent.height
                    Rectangle {
                        anchors.bottom: parent.bottom; width: parent.width
                        height: Math.max(Style.space(2), (parent.height - Style.space(2)) * Display.metricRatio(dayBar.modelData.metric, root.maximum))
                        color: !dayBar.modelData.covered ? Qt.alpha(Commons.Color.foreground, 0.07)
                            : dayBar.modelData.metric.value === null ? Qt.alpha(Commons.Color.foreground, 0.2)
                            : dayBar.modelData.metric.value === 0 ? Qt.alpha(Commons.Color.accent, 0.25) : Commons.Color.accent
                    }
                    HoverHandler { id: dayHover }
                    Controls.ToolTip {
                        visible: dayHover.hovered
                        text: dayBar.modelData.date + " UTC · " + (!dayBar.modelData.covered ? "Before collection" : Display.metricText(dayBar.modelData.metric, true) + " tokens")
                    }
                    Accessible.role: Accessible.StaticText
                    Accessible.name: dayBar.modelData.date + ": " + (dayBar.modelData.covered ? Display.metricText(dayBar.modelData.metric, true) + " tokens" : "Before collection")
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: Display.utcDate(root.view.from); muted: true; font.pixelSize: Style.font.caption }
            Item { Layout.fillWidth: true }
            Label { text: "DAILY TOKENS"; muted: true; font.pixelSize: Style.font.caption }
            Item { Layout.fillWidth: true }
            Label { text: "Today"; muted: true; font.pixelSize: Style.font.caption }
        }
    }
    Label {
        Layout.fillWidth: true; visible: !root.view.available || root.view.partial
        text: !root.view.available ? (root.history.reason || "Update the server collector to enable daily and model history. Lifetime totals are preserved.")
            : "Partial period · history since " + Display.utcDate(root.view.since) + (root.history.partial ? ". Some usage was not recorded." : ". Earlier usage is unavailable.")
        wrapMode: Text.Wrap; muted: true; font.pixelSize: Style.font.caption
    }
    Label {
        visible: !!root.snapshotData.collectorNotice; Layout.fillWidth: true
        text: root.snapshotData.collectorNotice || ""; wrapMode: Text.Wrap
        font.pixelSize: Style.font.caption; color: Commons.Color.urgent
    }
    Ui.PanelSeparator { Layout.fillWidth: true }
    RowLayout {
        visible: root.view.available; Layout.fillWidth: true
        Ui.ButtonGroup {
            objectName: "usageBreakdown"; value: root.breakdown
            options: [{value:"model",label:"Models"}, {value:"provider",label:"Providers"}]
            onChanged: value => root.breakdown = value
        }
        Item { Layout.fillWidth: true }
        Label { text: root.groups.length + (root.breakdown === "model" ? " models" : " providers"); muted: true; font.pixelSize: Style.font.caption }
    }
    Label { visible: !root.view.available && root.groups.length > 0; text: root.snapshotData.usageSource === "collector" ? "BY PROVIDER · LIFETIME" : "BY PROVIDER · SNAPSHOT"; muted: true; font.pixelSize: Style.font.caption }
    Repeater {
        model: root.groups
        ColumnLayout {
            id: group
            required property var modelData
            readonly property bool expanded: root.expandedGroup === modelData.key
            Layout.fillWidth: true; spacing: Style.space(5)
            Ui.CursorSurface {
                id: header
                objectName: "usageGroup_" + group.modelData.key
                Layout.fillWidth: true; implicitHeight: identity.implicitHeight + Style.space(6)
                activeFocusOnTab: true; hasCursor: activeFocus || hover.hovered
                function toggle() { root.expandedGroup = group.expanded ? "" : group.modelData.key }
                Accessible.role: Accessible.Button
                Accessible.name: group.modelData.label + ", " + Display.metricText(group.modelData.tokenMetrics.total, true) + " tokens, " + (group.expanded ? "hide" : "show") + " account breakdown"
                Accessible.onPressAction: toggle()
                Keys.onReturnPressed: toggle()
                Keys.onEnterPressed: toggle()
                Keys.onSpacePressed: toggle()
                HoverHandler { id: hover }
                TapHandler { onTapped: { header.forceActiveFocus(); header.toggle() } }
                RowLayout {
                    id: identity
                    anchors.fill: parent; anchors.topMargin: Style.space(3); anchors.bottomMargin: Style.space(3); spacing: Style.space(7)
                    Repeater { model: group.modelData.providers.slice(0, 2); ProviderLogo { required property string modelData; provider: modelData } }
                    Label { visible: group.modelData.providers.length > 2; text: "+" + (group.modelData.providers.length - 2); muted: true; font.pixelSize: Style.font.caption }
                    Label { Layout.fillWidth: true; text: group.modelData.label === "unknown" ? "Unknown model" : group.modelData.label; elide: Text.ElideRight; font.bold: true }
                    Label { text: Display.metricText(group.modelData.tokenMetrics.total); font.bold: true }
                    Label { text: group.expanded ? "−" : "+"; muted: true }
                }
                Controls.ToolTip { visible: hover.hovered; text: group.modelData.label + " · " + Display.metricText(group.modelData.tokenMetrics.total, true) + " tokens" }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: Style.space(3); color: Qt.alpha(Commons.Color.foreground, 0.08)
                Rectangle { height: parent.height; width: parent.width * Display.metricRatio(group.modelData.tokenMetrics.total, root.totals.tokenMetrics.total.value); color: Commons.Color.accent }
            }
            RowLayout {
                visible: !group.expanded; Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true; font.pixelSize: Style.font.caption; muted: true; elide: Text.ElideRight
                    text: Display.metricText(group.modelData.tokenMetrics.input) + " in · " + Display.metricText(group.modelData.tokenMetrics.output) + " out · " + Display.metricText(group.modelData.tokenMetrics.cached) + " cached"
                }
                Label { text: Display.tokenShare(group.modelData.tokenMetrics.total, root.totals.tokenMetrics.total); muted: true; font.pixelSize: Style.font.caption }
            }
            UsageMetrics { visible: group.expanded; Layout.fillWidth: true; summary: group.modelData; detailed: true }
            Repeater {
                model: group.expanded ? group.modelData.accounts : []
                ColumnLayout {
                    id: accountRow
                    required property var modelData
                    Layout.fillWidth: true; Layout.leftMargin: Style.space(10); spacing: Style.space(5)
                    RowLayout {
                        Layout.fillWidth: true; spacing: Style.space(6)
                        ProviderLogo { provider: accountRow.modelData.provider; size: Style.space(14) }
                        Label { Layout.fillWidth: true; text: accountRow.modelData.label; elide: Text.ElideMiddle; font.pixelSize: Style.font.bodySmall }
                        Label { text: Display.metricText(accountRow.modelData.tokenMetrics.total); font.pixelSize: Style.font.bodySmall }
                    }
                    UsageMetrics { Layout.fillWidth: true; summary: accountRow.modelData; detailed: root.detailsOpen }
                }
            }
            Item { implicitHeight: Style.space(3) }
        }
    }
    Label {
        visible: root.view.available && !root.groups.length; Layout.fillWidth: true; wrapMode: Text.Wrap
        text: "No token usage recorded in this period yet."; muted: true
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; muted: true; font.pixelSize: Style.font.caption
        text: "Cache and reasoning can overlap input/output."
            + (root.view.available ? " Bars show share of reported tokens." : "")
            + "\n* Partial data · — not reported"
    }
    component Label: Text {
        property bool muted: false
        textFormat: Text.PlainText; color: Qt.alpha(Commons.Color.foreground, muted ? 0.6 : 1)
        font.family: Style.font.family; font.pixelSize: Style.font.body
    }
}
