pragma Singleton
import QtQuick
import "qrc:/goodbot/Model.js" as Model

QtObject {
    property var panel: null
    function sourcePageIndex(source, entry) { return Model.sourcePageIndex(source, entry); }
    function openInbox() {
        if (!panel) return;
        panel.showPopup();
        panel.client.pane = "inbox";
        console.log("GOODBOT_INBOX_OPENED");
    }
    function capture(image, notebook, page, title, label, rect, bounds) {
        if (!panel) return;
        panel.showPopup();
        var client = panel.client;
        if (!client.loaded || client.capture || client.replyTo || client.directChat) {
            client.notice = "Finish or send your current draft before capturing another selection.";
            return;
        }
        try {
            var handoff = Model.selectionCapture(client.nativeEndpoint.encodeImage(image),
                notebook, page, title, label, rect, bounds);
            client.receive(2, JSON.stringify(handoff));
            console.log("GOODBOT_SELECTION_CAPTURED");
        } catch (error) {
            client.notice = String(error);
        }
    }
}
