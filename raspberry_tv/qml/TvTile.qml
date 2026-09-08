import QtQuick
import QtQuick.Controls

AbstractButton {
    id: tile
    property real unit: 1
    property string appId: ""
    property bool running: false
    activeFocusOnTab: true
    hoverEnabled: true
    width: (activeFocus ? 346 : 330) * unit
    height: 246 * unit
    scale: activeFocus ? 1.025 : 1
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    background: Rectangle {
        radius: 28 * tile.unit
        color: "#292929"
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            visible: tile.activeFocus
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "#ff1841" }
                GradientStop {
                    position: 0.5
                    SequentialAnimation on color {
                        running: tile.activeFocus
                        loops: Animation.Infinite
                        ColorAnimation { from: "#ff1841"; to: "#ffffff"; duration: 1600 }
                        ColorAnimation { from: "#ffffff"; to: "#ff1841"; duration: 1600 }
                    }
                }
                GradientStop { position: 1; color: "#ffffff" }
            }
        }
        Rectangle {
            anchors.fill: parent
            anchors.margins: tile.activeFocus ? 2.5 * tile.unit : 1 * tile.unit
            radius: parent.radius - 2 * tile.unit
            color: "#111111"
        }
    }
    contentItem: Item {
        Icon {
            name: tile.appId
            width: 90 * tile.unit; height: width
            anchors.horizontalCenter: parent.horizontalCenter
            y: 44 * tile.unit
            ink: tile.appId === "youtube" && tile.activeFocus ? "#ff1841" : "white"
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom; anchors.bottomMargin: 39 * tile.unit
            text: tile.text
            color: "white"
            font.pixelSize: 25 * tile.unit
        }
        Rectangle {
            visible: tile.running
            width: 5 * tile.unit; height: width; radius: width / 2
            color: "#ff1841"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom; anchors.bottomMargin: 21 * tile.unit
        }
    }
    onHoveredChanged: if (hovered) forceActiveFocus(Qt.MouseFocusReason)
}
