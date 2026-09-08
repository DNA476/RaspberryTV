import QtQuick
import QtQuick.Controls

Button {
    id: control
    property real unit: 1
    property bool accent: false
    hoverEnabled: true
    activeFocusOnTab: true
    font.pixelSize: 23 * unit
    font.weight: Font.Normal
    padding: 14 * unit
    horizontalPadding: 24 * unit
    implicitHeight: 58 * unit
    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? (control.activeFocus ? "#000000" : "#ffffff") : "#949494"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: height / 2
        color: control.activeFocus ? "white" : control.accent ? "#ff1841" : control.hovered ? "#212121" : "transparent"
        border.color: control.activeFocus ? "white" : control.accent ? "#ff1841" : "#494949"
        border.width: Math.max(1, control.unit)
        Behavior on color { ColorAnimation { duration: 110 } }
    }
    onHoveredChanged: if (hovered) forceActiveFocus(Qt.MouseFocusReason)
}
