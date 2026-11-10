import QtQuick
import qs.Common
import qs.Widgets

// Compact field: the icon and placeholder name the field, no separate label row.
Column {
    id: root
    property string label: ""
    property string icon: ""
    property string hint: ""
    property alias text: input.text
    property bool secret: false
    property alias validator: input.validator
    spacing: Theme.spacingXS
    DankTextField {
        id: input
        width: parent.width
        placeholderText: root.label
        leftIconName: root.icon
        leftIconSize: 18
        echoMode: root.secret ? TextInput.Password : TextInput.Normal
        showPasswordToggle: root.secret
        maximumLength: 4096
        Accessible.name: root.label
    }
    StyledText {
        visible: root.hint !== ""
        width: parent.width
        leftPadding: Theme.spacingM
        rightPadding: Theme.spacingM
        text: root.hint
        color: Theme.surfaceVariantText
        font.pixelSize: Theme.fontSizeSmall
        wrapMode: Text.WordWrap
        elide: Text.ElideNone
    }
}
