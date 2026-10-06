import QtQuick
import QtQuick.Layouts
import qs.Commons
import "Display.js" as Display

ColumnLayout {
    id: root
    property var windows: []
    property double now: Date.now()
    spacing: Style.space(14)
    Repeater {
        model: root.windows
        ColumnLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: Style.space(6)
            readonly property bool known: modelData.usedPercent !== null && modelData.usedPercent !== undefined
            readonly property real remaining: known ? Math.max(0, Math.min(100, 100 - modelData.usedPercent)) : 0
            readonly property color tint: remaining <= 10 && known ? Color.urgent : Color.foreground
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: modelData.label; textFormat: Text.PlainText
                    color: Color.foreground; font.family: Style.font.family; font.pixelSize: Style.font.body
                    Layout.fillWidth: true
                }
                Text {
                    text: known ? remaining.toFixed(0) + "% left" : "Not reported"; textFormat: Text.PlainText
                    color: tint; font.family: Style.font.family; font.pixelSize: Style.font.body; font.bold: true
                }
            }
            Row {
                Layout.fillWidth: true
                height: Style.space(7)
                spacing: Style.space(3)
                Repeater {
                    model: 32
                    Rectangle {
                        required property int index
                        width: Math.max(1, (parent.width - 31 * parent.spacing) / 32)
                        height: parent.height
                        color: tint
                        opacity: known && index < Math.round(remaining / 100 * 32) ? 0.85 : 0.12
                    }
                }
            }
            Text {
                text: Display.reset(modelData.resetAt, root.now); textFormat: Text.PlainText
                color: Qt.alpha(Color.foreground, 0.55); font.family: Style.font.family; font.pixelSize: Style.font.caption
            }
        }
    }
}
