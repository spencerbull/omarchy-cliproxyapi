import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
import qs.Commons as Commons
import "Display.js" as Display

Item {
    id: root
    property var buckets: []
    property color foreground: Commons.Color.foreground
    property bool compact: false
    readonly property real peak: Math.max(1, ...buckets.map(row => Number(row.metric.value || 0)))
    readonly property int bucketCount: buckets.length || 1
    implicitHeight: Style.space(compact ? 18 : 34)
    Accessible.role: Accessible.Chart
    Accessible.name: "Daily total token usage over seven UTC days"
    Row {
        anchors.fill: parent
        spacing: Style.space(3)
        Repeater {
            model: root.buckets
            Item {
                id: bucket
                required property var modelData
                width: Math.max(1, (parent.width - (root.bucketCount - 1) * parent.spacing) / root.bucketCount)
                height: parent.height
                readonly property real volumeHeight: modelData.metric.value > 0 ? Math.max(Style.space(4), height * modelData.metric.value / root.peak) : Style.space(2)
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width; height: bucket.volumeHeight
                    color: root.foreground
                    opacity: bucket.modelData.metric.value > 0 ? 0.7 : bucket.modelData.metric.value === 0 ? 0.2 : 0.07
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    Controls.ToolTip.visible: containsMouse
                    Controls.ToolTip.delay: 100
                    Controls.ToolTip.text: bucket.modelData.date + " UTC\n" + (!bucket.modelData.covered ? "Before collection" : bucket.modelData.metric.value === null ? "Tokens not reported" : Display.count(bucket.modelData.metric.value) + " total tokens" + (bucket.modelData.metric.partial ? " · partial" : ""))
                }
            }
        }
    }
}
