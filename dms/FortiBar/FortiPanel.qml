import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PopoutComponent {
    id: panel
    required property var backend
    property string page: "connections"
    property var editing: ({})
    property bool advanced: false
    property string caPem: ""
    property string caName: ""
    property string panelId: String(Math.random())
    readonly property var state: backend.state
    readonly property var selected: state.selected || ({})
    readonly property var profiles: state.profiles || []
    readonly property bool busy: !!state.busy
    readonly property bool connected: state.status === "connected"
    readonly property string statusText: ({disconnected: "Not connected", connected: "Connected", connecting: "Connecting…", disconnecting: "Disconnecting…", reconnecting: "Reconnecting…", unlocking: "Unlocking keyring…"})[state.status] || "Not connected"
    readonly property bool shown: parentPopout ? parentPopout.shouldBeVisible : visible
    readonly property string title: page === "edit" ? (editing.name ? "Edit connection" : "New connection") : page === "password" ? "Password required" : page === "certificate" ? "Untrusted certificate" : page === "activity" ? "Activity" : page === "remove" ? "Remove connection" : "VPN"
    // The DMS default header (40px, bold 20px) is oversized for this menu; draw a compact one.
    headerText: ""
    showCloseButton: false

    onShownChanged: {
        backend.setPanelVisible(panelId, shown)
        if (!shown) {
            password.text = ""
            loginPassword.text = ""
            page = "connections"
        }
    }
    Component.onCompleted: backend.setPanelVisible(panelId, shown)
    Component.onDestruction: backend.setPanelVisible(panelId, false)

    function edit(profile) {
        editing = profile
        connectionName.text = profile.name || ""
        host.text = profile.host || ""
        port.text = String(profile.port || 443)
        username.text = profile.username || ""
        password.text = ""
        remember.checked = !!profile.remember_password
        realm.text = profile.realm || ""
        fingerprints.text = (profile.trusted_certs || []).join(", ")
        caPem = profile.ca_pem || ""
        caName = profile.ca_name || ""
        caPath.text = ""
        advanced = !!(realm.text || fingerprints.text || caPem)
        page = "edit"
    }

    function save(connectAfter) {
        const raw = {id: editing.id, name: connectionName.text, host: host.text, port: port.text,
                     username: username.text, realm: realm.text, trusted_certs: fingerprints.text,
                     ca_pem: caPem, ca_name: caName, remember_password: remember.checked}
        const value = password.text
        password.text = ""
        backend.request("saveProfile", [raw, value, connectAfter], ok => { if (ok) page = "connections" })
    }

    function back() {
        password.text = ""
        loginPassword.text = ""
        page = page === "remove" ? "edit" : "connections"
    }

    function activate(profile) {
        if (busy) {
            if (selected.id === profile.id)
                backend.request("toggleConnection")
            return
        }
        backend.request("selectProfile", [profile.id])
        backend.request("toggleConnection")
    }

    Connections {
        target: panel.backend
        function onEditRequested(profile) { panel.edit(profile) }
        function onPasswordRequested(name) { loginPassword.text = ""; loginRemember.checked = !!panel.selected.remember_password; panel.page = "password" }
        function onCertificateRequested() { panel.page = "certificate" }
    }

    // ── Building blocks (sizes follow DMS's own VPN popout) ──────────────

    component IconButton: Rectangle {
        id: iconButton
        property string icon: ""
        property bool danger: false
        property bool active: true
        readonly property bool hovered: iconMouse.containsMouse
        signal clicked
        implicitWidth: 28
        implicitHeight: 28
        radius: 14
        opacity: active ? 1 : 0.4
        color: !iconMouse.containsMouse ? "transparent" : danger ? Theme.errorHover : Theme.surfaceTextHover
        DankIcon { anchors.centerIn: parent; name: iconButton.icon; size: 18; color: iconButton.danger && iconMouse.containsMouse ? Theme.error : Theme.surfaceVariantText }
        MouseArea { id: iconMouse; anchors.fill: parent; hoverEnabled: true; enabled: iconButton.active; cursorShape: Qt.PointingHandCursor; onClicked: iconButton.clicked() }
    }

    component PillButton: Rectangle {
        id: pill
        property string icon: ""
        property string text: ""
        property bool danger: false
        signal clicked
        implicitWidth: pillRow.implicitWidth + Theme.spacingM * 2
        implicitHeight: 28
        radius: 14
        color: pillMouse.containsMouse ? (danger ? Theme.errorHover : Theme.primaryHoverLight) : Theme.surfaceLight
        Row {
            id: pillRow
            anchors.centerIn: parent
            spacing: Theme.spacingXS
            DankIcon { anchors.verticalCenter: parent.verticalCenter; name: pill.icon; size: Theme.fontSizeSmall + 2; color: pill.danger ? Theme.error : Theme.primary; visible: pill.icon !== "" }
            StyledText { anchors.verticalCenter: parent.verticalCenter; text: pill.text; font.pixelSize: Theme.fontSizeSmall; font.weight: Font.Medium; color: pill.danger ? Theme.error : Theme.primary }
        }
        MouseArea { id: pillMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: pill.clicked() }
    }

    // 36px dialog button: outlined by default, filled when primary.
    component ActionButton: Rectangle {
        id: actionButton
        property string text: ""
        property bool primary: false
        property bool danger: false
        property bool active: true
        signal clicked
        readonly property color fill: danger ? Theme.error : Theme.primary
        implicitWidth: Math.max(80, actionLabel.implicitWidth + Theme.spacingL * 2)
        implicitHeight: 36
        radius: Theme.cornerRadius
        opacity: active ? 1 : 0.4
        color: primary ? (actionMouse.containsMouse ? Qt.darker(fill, 1.1) : fill) : (actionMouse.containsMouse ? Theme.surfaceTextHover : "transparent")
        border.width: primary ? 0 : 1
        border.color: Theme.surfaceVariantAlpha
        StyledText { id: actionLabel; anchors.centerIn: parent; text: actionButton.text; font.pixelSize: Theme.fontSizeMedium; font.weight: Font.Medium; color: actionButton.primary ? Theme.background : Theme.surfaceText }
        MouseArea { id: actionMouse; anchors.fill: parent; hoverEnabled: true; enabled: actionButton.active; cursorShape: Qt.PointingHandCursor; onClicked: actionButton.clicked() }
    }

    component CheckRow: Item {
        id: check
        property string text: ""
        property bool checked: false
        implicitWidth: checkRow.implicitWidth
        implicitHeight: 28
        Row {
            id: checkRow
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacingS
            Rectangle {
                width: 18; height: 18; radius: 4
                anchors.verticalCenter: parent.verticalCenter
                color: check.checked ? Theme.primary : "transparent"
                border.width: 2
                border.color: check.checked ? Theme.primary : Theme.outlineButton
                DankIcon { anchors.centerIn: parent; name: "check"; size: 14; color: Theme.background; visible: check.checked }
            }
            StyledText { anchors.verticalCenter: parent.verticalCenter; text: check.text; font.pixelSize: Theme.fontSizeMedium; color: Theme.surfaceText }
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: check.checked = !check.checked }
    }

    // Compact switch: DankToggle is fixed at 52x30, too heavy for a text row.
    component MiniSwitch: Rectangle {
        id: sw
        property bool checked: false
        signal toggled(bool checked)
        implicitWidth: 34
        implicitHeight: 20
        radius: 10
        color: checked ? Theme.primary : Theme.surfaceVariantAlpha
        border.width: checked ? 0 : 1
        border.color: Theme.outline
        Rectangle {
            width: sw.checked ? 14 : 12
            height: width
            radius: width / 2
            anchors.verticalCenter: parent.verticalCenter
            x: sw.checked ? sw.width - width - 3 : 4
            color: sw.checked ? Theme.background : Theme.outline
            Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sw.toggled(!sw.checked) }
    }

    component Divider: Rectangle {
        width: parent ? parent.width : 0
        height: 1
        color: Theme.outlineStrong
    }

    component ButtonRow: Row {
        anchors.right: parent ? parent.right : undefined
        spacing: Theme.spacingS
        layoutDirection: Qt.RightToLeft
    }

    // ── Content ──────────────────────────────────────────────────────────

    Column {
        width: parent.width
        spacing: Theme.spacingM

        // Header
        RowLayout {
            width: parent.width
            height: 32
            spacing: Theme.spacingXS
            IconButton { visible: panel.page !== "connections"; icon: "arrow_back"; onClicked: panel.back() }
            StyledText {
                Layout.fillWidth: true
                Layout.leftMargin: panel.page === "connections" ? Theme.spacingXS : 0
                text: panel.title
                font.pixelSize: Theme.fontSizeLarge
                font.weight: Font.Medium
                color: Theme.surfaceText
                elide: Text.ElideRight
            }
            IconButton { visible: panel.page === "connections"; icon: "history"; onClicked: panel.page = "activity" }
            IconButton { visible: panel.page === "connections"; icon: "add"; active: !panel.busy; onClicked: panel.backend.request("newProfile") }
            IconButton { visible: panel.page === "edit" && !!panel.editing.name; icon: "delete"; danger: true; onClicked: panel.page = "remove" }
        }

        // Error / notice
        Rectangle {
            readonly property bool isError: !!(panel.state.error || panel.backend.bridgeError)
            visible: !!(panel.state.error || panel.state.notice || panel.backend.bridgeError)
            width: parent.width
            height: bannerText.implicitHeight + Theme.spacingS * 2
            radius: Theme.cornerRadius
            color: isError ? Theme.errorHover : Theme.surfaceLight
            DankIcon { id: bannerIcon; x: Theme.spacingM; y: Theme.spacingS; name: parent.isError ? "error" : "info"; size: 16; color: parent.isError ? Theme.error : Theme.surfaceVariantText }
            StyledText {
                id: bannerText
                anchors { left: bannerIcon.right; right: parent.right; top: parent.top; leftMargin: Theme.spacingS; rightMargin: Theme.spacingM; topMargin: Theme.spacingS }
                text: panel.state.error || panel.backend.bridgeError || panel.state.notice || ""
                color: parent.isError ? Theme.error : Theme.surfaceVariantText
                font.pixelSize: Theme.fontSizeSmall
                wrapMode: Text.WordWrap
                elide: Text.ElideNone
            }
        }

        // ── Connections ──
        Rectangle {
            visible: panel.page === "connections"
            width: parent.width
            height: listColumn.implicitHeight + Theme.spacingM * 2
            radius: Theme.cornerRadius
            color: Theme.withAlpha(Theme.surfaceContainerHigh, Theme.popupTransparency)

            Column {
                id: listColumn
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.spacingM }
                spacing: Theme.spacingS

                RowLayout {
                    width: parent.width
                    height: 28
                    spacing: Theme.spacingS
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: panel.connected ? Theme.primary : panel.busy ? Theme.primaryContainer : Theme.outlineButton
                    }
                    StyledText { Layout.fillWidth: true; text: panel.statusText; font.pixelSize: Theme.fontSizeMedium; font.weight: Font.Medium; color: Theme.surfaceText; elide: Text.ElideRight }
                    PillButton {
                        visible: panel.busy && panel.state.status !== "disconnecting"
                        icon: panel.connected ? "link_off" : "close"
                        text: panel.connected ? "Disconnect" : "Cancel"
                        danger: panel.connected
                        onClicked: panel.backend.request("toggleConnection")
                    }
                }

                Divider {}

                Column {
                    width: parent.width
                    spacing: 4
                    visible: panel.profiles.length > 0
                    Repeater {
                        model: panel.profiles
                        delegate: Rectangle {
                            id: row
                            required property var modelData
                            readonly property bool isSelected: panel.selected.id === modelData.id
                            readonly property bool isLive: isSelected && panel.busy
                            readonly property bool hovered: rowMouse.containsMouse || editButton.hovered
                            width: parent.width
                            height: 46
                            radius: Theme.cornerRadius
                            color: hovered && (!panel.busy || isSelected) ? Theme.primaryHoverLight : isLive ? Theme.primaryPressed : Theme.surfaceLight
                            border.width: isLive ? 2 : 1
                            border.color: isLive ? Theme.primary : Theme.outlineLight
                            opacity: panel.busy && !isSelected ? 0.5 : 1
                            MouseArea {
                                id: rowMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: panel.busy && !row.isSelected ? Qt.ArrowCursor : Qt.PointingHandCursor
                                onClicked: panel.activate(row.modelData)
                            }
                            RowLayout {
                                anchors { fill: parent; leftMargin: Theme.spacingM; rightMargin: Theme.spacingS }
                                spacing: Theme.spacingM
                                DankIcon {
                                    name: row.isLive && panel.connected ? "vpn_lock" : row.isLive ? "sync" : "vpn_key"
                                    size: 20
                                    color: row.isLive ? Theme.primary : Theme.surfaceVariantText
                                }
                                Column {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    StyledText { width: parent.width; text: row.modelData.name; font.pixelSize: Theme.fontSizeMedium; color: row.isLive ? Theme.primary : Theme.surfaceText; elide: Text.ElideRight }
                                    StyledText {
                                        width: parent.width
                                        text: row.isLive && panel.connected ? [panel.state.localIp, panel.state.elapsed].filter(v => !!v).join(" · ") || row.modelData.endpoint : row.isLive ? panel.statusText : row.modelData.endpoint
                                        font.pixelSize: Theme.fontSizeSmall
                                        color: Theme.surfaceTextMedium
                                        elide: Text.ElideRight
                                    }
                                }
                                IconButton { id: editButton; icon: "edit"; active: !panel.busy; onClicked: { panel.backend.request("selectProfile", [row.modelData.id]); panel.backend.request("editProfile") } }
                            }
                        }
                    }
                }

                Column {
                    visible: panel.profiles.length === 0
                    width: parent.width
                    topPadding: Theme.spacingL
                    bottomPadding: Theme.spacingL
                    spacing: Theme.spacingXS
                    DankIcon { anchors.horizontalCenter: parent.horizontalCenter; name: "vpn_key_off"; size: 32; color: Theme.surfaceVariantText }
                    StyledText { anchors.horizontalCenter: parent.horizontalCenter; text: "No VPN connections"; font.pixelSize: Theme.fontSizeMedium; color: Theme.surfaceVariantText }
                    PillButton { anchors.horizontalCenter: parent.horizontalCenter; icon: "add"; text: "Add connection"; onClicked: panel.backend.request("newProfile") }
                }

                Divider {}

                RowLayout {
                    width: parent.width
                    height: 24
                    StyledText { Layout.fillWidth: true; text: "Reconnect automatically"; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceTextMedium }
                    MiniSwitch { Layout.rightMargin: Theme.spacingXS; checked: !!panel.state.retryEnabled; onToggled: value => panel.backend.request("setRetryEnabled", [value]) }
                }

                StyledText {
                    visible: panel.backend.available && !panel.state.setupReady
                    width: parent.width
                    text: panel.state.setupCommand || "Install the Forti Bar host package to connect."
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.surfaceTextMedium
                    wrapMode: Text.WordWrap
                    elide: Text.ElideNone
                }
            }
        }

        // ── Edit ──
        Column {
            width: parent.width
            visible: panel.page === "edit"
            spacing: Theme.spacingS
            FortiField { id: connectionName; width: parent.width; icon: "label"; label: "Connection name" }
            RowLayout {
                width: parent.width
                spacing: Theme.spacingS
                FortiField { id: host; Layout.fillWidth: true; icon: "dns"; label: "Host (vpn.company.com)" }
                FortiField { id: port; Layout.preferredWidth: 84; label: "Port"; validator: IntValidator { bottom: 1; top: 65535 } }
            }
            FortiField { id: username; width: parent.width; icon: "person"; label: "Username" }
            FortiField { id: password; width: parent.width; icon: "key"; label: panel.editing.remember_password ? "Password (saved)" : "Password"; secret: true }
            RowLayout {
                width: parent.width
                CheckRow { id: remember; text: "Remember password" }
                Item { Layout.fillWidth: true }
                Item {
                    implicitWidth: advancedRow.implicitWidth
                    implicitHeight: 28
                    Row {
                        id: advancedRow
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        StyledText { anchors.verticalCenter: parent.verticalCenter; text: "Advanced"; font.pixelSize: Theme.fontSizeSmall; font.weight: Font.Medium; color: advancedMouse.containsMouse ? Theme.primary : Theme.surfaceTextMedium }
                        DankIcon { anchors.verticalCenter: parent.verticalCenter; name: panel.advanced ? "expand_less" : "expand_more"; size: 18; color: advancedMouse.containsMouse ? Theme.primary : Theme.surfaceTextMedium }
                    }
                    MouseArea { id: advancedMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: panel.advanced = !panel.advanced }
                }
            }
            Column {
                width: parent.width
                visible: panel.advanced
                spacing: Theme.spacingS
                FortiField { id: realm; width: parent.width; icon: "domain"; label: "Realm (optional)" }
                FortiField { id: fingerprints; width: parent.width; icon: "fingerprint"; label: "Trusted SHA-256 fingerprints"; hint: "Comma-separated. Only used when normal verification fails." }
                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingS
                    FortiField { id: caPath; Layout.fillWidth: true; icon: "description"; label: panel.caName ? "CA: " + panel.caName : "CA certificate path (optional)" }
                    IconButton { visible: !!panel.caPem; icon: "close"; onClicked: { panel.caPem = ""; panel.caName = "" } }
                    PillButton {
                        icon: "folder_open"; text: "Load"
                        opacity: caPath.text.length > 0 ? 1 : 0.4
                        onClicked: if (caPath.text.length > 0) panel.backend.request("readCa", [caPath.text], ca => { if (ca && ca.pem) { panel.caPem = ca.pem; panel.caName = ca.name; caPath.text = "" } })
                    }
                }
            }
            Item { width: 1; height: Theme.spacingXS }
            Item {
                width: parent.width
                height: 36
                ButtonRow {
                    ActionButton { primary: true; text: "Save & connect"; onClicked: panel.save(true) }
                    ActionButton { text: "Save"; onClicked: panel.save(false) }
                }
            }
        }

        // ── Password ──
        Column {
            visible: panel.page === "password"
            width: parent.width
            spacing: Theme.spacingS
            StyledText {
                width: parent.width
                text: (panel.selected.name || "") + (panel.selected.endpoint ? " · " + panel.selected.endpoint : "")
                font.pixelSize: Theme.fontSizeMedium
                color: Theme.surfaceTextMedium
                elide: Text.ElideRight
            }
            FortiField { id: loginPassword; width: parent.width; icon: "key"; label: "Password"; secret: true }
            CheckRow { id: loginRemember; text: "Remember password" }
            Item { width: 1; height: Theme.spacingXS }
            Item {
                width: parent.width
                height: 36
                ButtonRow {
                    ActionButton {
                        primary: true; text: "Connect"; active: !!loginPassword.text
                        onClicked: { const value = loginPassword.text; loginPassword.text = ""; panel.page = "connections"; panel.backend.request("providePassword", [value, loginRemember.checked]) }
                    }
                    ActionButton { text: "Cancel"; onClicked: panel.back() }
                }
            }
        }

        // ── Certificate ──
        Column {
            visible: panel.page === "certificate"
            width: parent.width
            spacing: Theme.spacingS
            StyledText {
                width: parent.width
                text: "Verify this fingerprint with your administrator before trusting " + (panel.selected.endpoint || "this gateway") + "."
                font.pixelSize: Theme.fontSizeMedium
                color: Theme.surfaceTextMedium
                wrapMode: Text.WordWrap
                elide: Text.ElideNone
            }
            Rectangle {
                width: parent.width
                height: fingerprintText.implicitHeight + Theme.spacingM * 2
                radius: Theme.cornerRadius
                color: Theme.surfaceLight
                border.width: 1
                border.color: Theme.outlineLight
                TextEdit {
                    id: fingerprintText
                    anchors { fill: parent; margins: Theme.spacingM }
                    text: (panel.state.certificate || {}).fingerprint || "—"
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere
                    color: Theme.primary; font.family: Theme.monoFontFamily; font.pixelSize: Theme.fontSizeSmall
                }
            }
            Item { width: 1; height: Theme.spacingXS }
            Item {
                width: parent.width
                height: 36
                ButtonRow {
                    ActionButton { primary: true; text: "Trust & connect"; onClicked: { panel.page = "connections"; panel.backend.request("trustCertificate") } }
                    ActionButton { text: "Cancel"; onClicked: panel.back() }
                }
            }
        }

        // ── Activity ──
        Rectangle {
            visible: panel.page === "activity"
            width: parent.width
            height: panel.state.logs ? 240 : 120
            radius: Theme.cornerRadius
            color: Theme.withAlpha(Theme.surfaceContainerHigh, Theme.popupTransparency)
            ScrollView {
                anchors { fill: parent; margins: Theme.spacingXS }
                visible: !!panel.state.logs
                TextArea { text: panel.state.logs || ""; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.surfaceVariantText; font.family: Theme.monoFontFamily; font.pixelSize: Theme.fontSizeSmall; background: Item {} }
            }
            Column {
                visible: !panel.state.logs
                anchors.centerIn: parent
                spacing: Theme.spacingXS
                DankIcon { anchors.horizontalCenter: parent.horizontalCenter; name: "history"; size: 32; color: Theme.surfaceVariantText }
                StyledText { anchors.horizontalCenter: parent.horizontalCenter; text: "No connection events yet"; font.pixelSize: Theme.fontSizeMedium; color: Theme.surfaceVariantText }
            }
        }

        // ── Remove ──
        Column {
            visible: panel.page === "remove"
            width: parent.width
            spacing: Theme.spacingS
            StyledText {
                width: parent.width
                text: "Remove \"" + (panel.selected.name || "this connection") + "\" and its saved password?"
                font.pixelSize: Theme.fontSizeMedium
                color: Theme.surfaceText
                wrapMode: Text.WordWrap
                elide: Text.ElideNone
            }
            Item { width: 1; height: Theme.spacingXS }
            Item {
                width: parent.width
                height: 36
                ButtonRow {
                    ActionButton { primary: true; danger: true; text: "Remove"; onClicked: panel.backend.request("deleteProfile", [], ok => { if (ok) panel.page = "connections" }) }
                    ActionButton { text: "Cancel"; onClicked: panel.back() }
                }
            }
        }
    }
}
