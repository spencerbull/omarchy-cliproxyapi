import QtQuick
import qs.Commons
import qs.Commons as Commons

Item {
    id: root
    property string provider: ""
    property int size: Style.space(18)
    implicitWidth: size; implicitHeight: size
    readonly property bool light: (Commons.Color.popups.background.r * 0.299 + Commons.Color.popups.background.g * 0.587 + Commons.Color.popups.background.b * 0.114) > 0.55
    readonly property string asset: ({codex: "codex" + (light ? "-light" : ""), claude: "claude", xai: "xai" + (light ? "-light" : ""), meta: "meta"})[provider] || ""
    Image {
        anchors.fill: parent
        visible: root.asset !== ""
        source: root.asset ? Qt.resolvedUrl("assets/" + root.asset + ".svg") : ""
        sourceSize: Qt.size(root.size * 2, root.size * 2)
        fillMode: Image.PreserveAspectFit
        smooth: true
    }
    Text {
        anchors.centerIn: parent; visible: root.asset === ""
        text: "󰒋"; color: Commons.Color.foreground
        font.family: Style.font.family; font.pixelSize: root.size
    }
}
