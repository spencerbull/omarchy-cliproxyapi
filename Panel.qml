import QtQuick
import qs.Commons
import qs.Ui

Panel {
    id: root
    moduleName: "spencerbull.cliproxyapi"
    manageIpc: false
    readonly property var service: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    onOpenedChanged: {
        if (service) { service.panelOpen = opened; if (opened && service.retryable) service.refresh() }
    }

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "󰒋"
        tooltipText: "CLIProxyAPI · " + (root.service && root.service.snapshot ? "Subscription limits" : "Set up connection")
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.MiddleButton && root.service) root.service.refresh(true)
            else root.toggle()
        }
    }

    KeyboardPanel {
        id: popup
        owner: root
        anchorItem: button
        bar: root.bar
        open: root.opened
        focusTarget: dashboard
        contentWidth: fittedContentWidth(Style.space(380))
        contentHeight: fittedContentHeight(dashboard.preferredHeight, Style.space(560))
        Dashboard {
            id: dashboard
            anchors.fill: parent
            service: root.service
            onDismiss: root.close()
        }
    }
}
