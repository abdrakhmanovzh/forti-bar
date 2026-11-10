import QtQuick
import qs.Common
import qs.Widgets
import qs.Modules.Plugins
import "." as Forti

PluginComponent {
    id: root
    layerNamespacePlugin: "forti-bar"
    popoutWidth: 376
    Component.onCompleted: Forti.Backend.attachWidget()
    Component.onDestruction: Forti.Backend.detachWidget()

    function openWithMode(mode) {
        closePopout()
        Qt.callLater(triggerPopout)
    }

    horizontalBarPill: Component {
        DankIcon {
            name: "vpn_key"
            size: root.iconSize
            color: Forti.Backend.state.status === "connected" ? Theme.primary : Theme.surfaceText
        }
    }
    verticalBarPill: horizontalBarPill

    popoutContent: Component {
        FortiPanel { backend: Forti.Backend }
    }
}
