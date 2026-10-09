import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Display.js" as Display

ColumnLayout {
    id: root
    required property var account
    property var quota: null
    property bool quotaBusy: false
    property bool quotaQueued: false
    property bool busy: false
    property bool expanded: false
    property bool privateMode: false
    property bool consentPending: false
    property bool consented: false
    property double now: Date.now()
    readonly property var windows: Display.quotaWindows(quota)
    readonly property string facts: Display.quotaFacts(quota, now)
    signal toggled()
    signal quotaRequested(bool consent)
    spacing: Style.space(7)

    Ui.CursorSurface {
        id: header
        Layout.fillWidth: true
        implicitHeight: identity.implicitHeight + Style.space(6)
        activeFocusOnTab: true; hasCursor: activeFocus || hover.hovered
        Accessible.role: Accessible.Button
        Accessible.name: Display.accountName(root.account, root.privateMode) + ", " + Display.providerName(root.account.provider) + (root.expanded ? ", hide details" : ", show activity details")
        Accessible.onPressAction: root.toggled()
        Keys.onReturnPressed: root.toggled()
        Keys.onEnterPressed: root.toggled()
        Keys.onSpacePressed: root.toggled()
        HoverHandler { id: hover }
        TapHandler { onTapped: { header.forceActiveFocus(); root.toggled() } }
        RowLayout {
            id: identity
            anchors.fill: parent; anchors.topMargin: Style.space(3); anchors.bottomMargin: Style.space(3)
            spacing: Style.space(8)
            ProviderLogo { provider: root.account.provider }
            ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(2)
                Label { Layout.fillWidth: true; text: Display.accountName(root.account, root.privateMode); font.bold: true; elide: Text.ElideMiddle }
                Label {
                    Layout.fillWidth: true
                    text: Display.providerName(root.account.provider) + (((root.quota && root.quota.plan) || root.account.plan) ? " · " + ((root.quota && root.quota.plan) || root.account.plan) : "")
                    color: Qt.alpha(Commons.Color.foreground, 0.5); font.pixelSize: Style.font.caption; elide: Text.ElideRight
                }
            }
            Label {
                visible: root.account.status !== "active"
                text: root.account.status === "unavailable" && root.account.nextRetryAt ? "Cooldown" : ({disabled: "Disabled", unavailable: "Unavailable", error: "Attention", unknown: ""})[root.account.status] || ""
                color: Commons.Color.urgent; font.pixelSize: Style.font.caption
            }
            Label { text: root.expanded ? "−" : "+"; color: Qt.alpha(Commons.Color.foreground, 0.4) }
        }
    }
    QuotaWindows { Layout.fillWidth: true; windows: root.windows; now: root.now }
    Label {
        Layout.fillWidth: true; visible: root.facts !== ""
        text: root.facts; wrapMode: Text.Wrap
        color: Qt.alpha(Commons.Color.foreground, 0.55); font.pixelSize: Style.font.caption
        HoverHandler { id: factsHover }
        Controls.ToolTip.visible: factsHover.hovered && Display.quotaDetails(root.quota) !== ""
        Controls.ToolTip.text: Display.quotaDetails(root.quota)
    }
    Label {
        Layout.fillWidth: true
        visible: root.windows.length === 0 && !(root.account.quotaConsentRequired && !root.consented)
        text: root.quotaBusy ? "Fetching limits…" : root.quotaQueued ? "Waiting to refresh…"
            : root.quota && root.quota.error ? root.quota.error
            : !root.account.quotaSupported ? (root.account.quotaReason || "No quota endpoint for this account.")
            : root.quota ? "No limits reported by this subscription." : "Waiting for limits…"
        color: root.quota && root.quota.error ? Commons.Color.urgent : Qt.alpha(Commons.Color.foreground, 0.5)
        font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap
    }
    Label {
        Layout.fillWidth: true; visible: root.windows.length > 0 && !!(root.quota && root.quota.error)
        text: "Showing previous limits · " + (root.quota ? root.quota.error || "" : "")
        color: Commons.Color.urgent; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap
    }
    ColumnLayout {
        visible: root.account.quotaConsentRequired === true && !root.consented
        Layout.fillWidth: true; spacing: Style.space(5)
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap
            text: root.consentPending ? "Muse returns limits by issuing an API key. Allow this lookup and automatic refresh for this session? The key stays out of the interface and storage." : "Muse requires permission to load limits."
            color: Qt.alpha(Commons.Color.foreground, 0.55); font.pixelSize: Style.font.bodySmall
        }
        RowLayout {
            Ui.Button {
                text: root.consentPending ? "Allow for this session" : "Set up Muse limits"
                focusable: true; enabled: !root.busy
                onClicked: {
                    if (root.consentPending) { root.quotaRequested(true); root.consentPending = false }
                    else root.consentPending = true
                }
            }
            Ui.Button { visible: root.consentPending; text: "Cancel"; focusable: true; onClicked: root.consentPending = false }
        }
    }
    ColumnLayout {
        visible: root.expanded
        Layout.fillWidth: true; spacing: Style.space(7)
        ActivityStrip { Layout.fillWidth: true; buckets: root.account.history || []; compact: true }
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true; elide: Text.ElideRight
                text: root.account.nextRetryAt ? (Display.resetShort(root.account.nextRetryAt, root.now) === "Due" ? "Retry available" : "Retry in " + Display.resetShort(root.account.nextRetryAt, root.now))
                    : root.account.lastActivityKind === "exact" ? "Last request " + Display.relative(root.account.lastRequestAt, root.now).toLowerCase()
                    : root.account.lastActivityKind === "window" ? "Last active ≈ " + Display.activity(root.account, root.now)
                    : "Last activity —"
                color: root.account.nextRetryAt ? Commons.Color.urgent : Qt.alpha(Commons.Color.foreground, 0.55)
                font.pixelSize: Style.font.caption
                HoverHandler { id: activityHover }
                Controls.ToolTip.visible: activityHover.hovered
                Controls.ToolTip.text: root.account.nextRetryAt ? "Retry available " + new Date(root.account.nextRetryAt).toLocaleString()
                    : root.account.lastActivityKind === "exact" ? new Date(root.account.lastRequestAt).toLocaleString()
                    : root.account.lastActivityKind === "window" ? "Approximate activity window · server time" : "Last request not reported"
            }
            Ui.Button {
                visible: root.account.quotaSupported === true && (!root.account.quotaConsentRequired || root.consented)
                text: root.quotaBusy ? "Checking…" : Display.retryWait(root.quota, root.now) > 0 ? "Wait " + Math.ceil(Display.retryWait(root.quota, root.now) / 60000) + "m" : "Refresh"
                tooltipText: root.quota && root.quota.updatedAt ? "Limits checked " + Display.relative(root.quota.updatedAt, root.now).toLowerCase() : "Refresh subscription limits"
                enabled: !root.busy && Display.retryWait(root.quota, root.now) <= 0; focusable: true
                onClicked: root.quotaRequested(false)
            }
        }
    }
    Item { implicitHeight: Style.space(2) }
    Ui.PanelSeparator { Layout.fillWidth: true }
    component Label: Text {
        textFormat: Text.PlainText; color: Commons.Color.foreground
        font.family: Style.font.family; font.pixelSize: Style.font.body
    }
}
