import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import "Display.js" as Display

ColumnLayout {
    id: root
    property var windows: []
    property double now: Date.now()
    spacing: Style.space(7)
    Repeater {
        model: root.windows
        RowLayout {
            id: row
            required property var modelData
            Layout.fillWidth: true
            spacing: Style.space(7)
            readonly property bool known: modelData.usedPercent !== null && modelData.usedPercent !== undefined
            readonly property real remaining: known ? Math.max(0, Math.min(100, 100 - modelData.usedPercent)) : 0
            readonly property string availability: Display.windowState(modelData)
            readonly property color tint: availability === "Blocked" || (remaining <= 10 && known && availability === "") ? Commons.Color.urgent : Commons.Color.foreground
            Accessible.role: Accessible.ProgressBar
            Accessible.name: modelData.label + ": " + (availability ? availability + ". " : "") + (known ? remaining.toFixed(0) + " percent remaining. " : "Not reported. ") + Display.reset(modelData.resetAt, root.now)
            Text {
                Layout.preferredWidth: Style.space(100)
                text: row.modelData.label; textFormat: Text.PlainText
                color: Qt.alpha(Commons.Color.foreground, 0.7); elide: Text.ElideRight
                font.family: Style.font.family; font.pixelSize: Style.font.bodySmall
                HoverHandler { id: labelHover }
                Controls.ToolTip.visible: labelHover.hovered
                Controls.ToolTip.text: row.modelData.label
            }
            Item {
                Layout.fillWidth: true; implicitHeight: Style.space(6)
                Row {
                    anchors.fill: parent; spacing: Style.space(2)
                    Repeater {
                        model: 24
                        Rectangle {
                            required property int index
                            width: Math.max(1, (parent.width - 23 * parent.spacing) / 24)
                            height: parent.height; color: row.tint
                            opacity: row.availability === "" && row.known && index < Math.round(row.remaining / 100 * 24) ? 0.8 : 0.12
                        }
                    }
                }
            }
            Text {
                Layout.preferredWidth: Style.space(54)
                text: row.availability || (row.known ? row.remaining.toFixed(0) + "%" : "—")
                color: row.tint; horizontalAlignment: Text.AlignRight
                font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; font.bold: row.availability === ""
                HoverHandler { id: availabilityHover }
                Controls.ToolTip.visible: availabilityHover.hovered
                Controls.ToolTip.text: row.availability ? row.availability + (row.known ? " · provider reports " + row.remaining.toFixed(0) + "% remaining" : "") : "Allowance remaining"
            }
            Text {
                Layout.preferredWidth: Style.space(55)
                text: Display.resetShort(row.modelData.resetAt, root.now)
                color: Qt.alpha(Commons.Color.foreground, 0.45); horizontalAlignment: Text.AlignRight
                font.family: Style.font.family; font.pixelSize: Style.font.caption
                HoverHandler { id: resetHover }
                Controls.ToolTip.visible: resetHover.hovered
                Controls.ToolTip.text: row.modelData.resetAt ? new Date(row.modelData.resetAt).toLocaleString() : "Reset time not reported"
            }
        }
    }
}
