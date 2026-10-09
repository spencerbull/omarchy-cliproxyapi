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
    readonly property real peak: Math.max(1, ...buckets.map(row => Number(row.requests || 0)))
    readonly property int bucketCount: buckets.length || 20
    implicitHeight: Style.space(compact ? 18 : 34)
    Accessible.role: Accessible.Chart
    Accessible.name: "Recent request activity"
    Row {
        anchors.fill: parent
        spacing: Style.space(3)
        Repeater {
            model: root.buckets.length ? root.buckets : new Array(20).fill({requests: 0, success: 0, failed: 0, label: "No recent history"})
            Item {
                id: bucket
                required property var modelData
                width: Math.max(1, (parent.width - (root.bucketCount - 1) * parent.spacing) / root.bucketCount)
                height: parent.height
                readonly property real volumeHeight: modelData.requests > 0 ? Math.max(Style.space(4), height * modelData.requests / root.peak) : Style.space(2)
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width; height: bucket.volumeHeight
                    color: root.foreground
                    opacity: bucket.modelData.requests > 0 ? 0.55 : 0.13
                }
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: bucket.modelData.requests > 0 ? bucket.volumeHeight * (bucket.modelData.failed || 0) / bucket.modelData.requests : 0
                    color: Commons.Color.urgent
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    Controls.ToolTip.visible: containsMouse
                    Controls.ToolTip.delay: 100
                    Controls.ToolTip.text: bucket.modelData.label + "\n" + Display.count(bucket.modelData.success) + " successful · " + Display.count(bucket.modelData.failed) + " failed"
                }
            }
        }
    }
}
