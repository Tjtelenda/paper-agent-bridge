import QtQuick
import xofm.libs.controls
import "qrc:/goodbot" as Client
import GoodBot.Bridge 1.0 as GoodBot

Popup {
    id: root
    property alias client: client
    property var sourceOpener: null
    anchors.fill: parent
    outsidePenInputBlocked: true
    // Closing must wait for durable draft storage; outside taps cannot discard it.
    tapOutsideBehavior: Popup.TapOutside.Ignore
    Component.onCompleted: GoodBot.Session.panel = root
    Component.onDestruction: if (GoodBot.Session.panel === root) GoodBot.Session.panel = null

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        preventStealing: true
        propagateComposedEvents: false
        onPressed: function(mouse) { mouse.accepted = true; }
        onClicked: function(mouse) { mouse.accepted = true; }
        onDoubleClicked: function(mouse) { mouse.accepted = true; }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 24
        color: "white"
        border.color: "black"
        border.width: 2
        Client.Main {
            id: client
            anchors.fill: parent
            anchors.margins: 12
            panelVisible: root.active
            onClose: root.hidePopup()
            onSourceOpenRequested: function(source) {
                if (root.sourceOpener) root.sourceOpener(source);
                else client.notice = "Source navigation is unavailable in this window.";
            }
        }
    }
}
