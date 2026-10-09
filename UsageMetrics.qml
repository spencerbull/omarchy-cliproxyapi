import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Display.js" as Display

Item {
    id: root
    required property var summary
    property bool detailed: false
    implicitHeight: Style.space(detailed ? 70 : 32)
    implicitWidth: 1
    Repeater {
        model: root.detailed ? [{key:"input",label:"INPUT"},{key:"output",label:"OUTPUT"},{key:"cached",label:"CACHED"},
            {key:"reasoning",label:"REASONING"},{key:"cacheRead",label:"CACHE READ"},{key:"cacheWrite",label:"CACHE WRITE"}]
            : [{key:"input",label:"INPUT"},{key:"output",label:"OUTPUT"},{key:"cached",label:"CACHED"}]
        ColumnLayout {
            id: cell
            required property var modelData
            required property int index
            readonly property var metric: root.summary.tokenMetrics[modelData.key]
            x: (index % 3) * root.width / 3
            y: Math.floor(index / 3) * Style.space(38)
            width: root.width / 3
            spacing: Style.space(2)
            Text { text: cell.modelData.label; color: Qt.alpha(Commons.Color.foreground, 0.6); font.family: Style.font.family; font.pixelSize: Style.font.caption }
            Text {
                text: Display.metricText(cell.metric); color: Commons.Color.foreground
                font.family: Style.font.family; font.pixelSize: Style.font.bodySmall
                Accessible.name: cell.modelData.label + ": " + Display.metricText(cell.metric, true)
            }
            HoverHandler { id: hover }
            Controls.ToolTip {
                visible: hover.hovered
                text: Display.metricText(cell.metric, true) + " tokens · " + cell.metric.reported + "/" + cell.metric.records + " records"
            }
        }
    }
}
