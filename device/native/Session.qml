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
    function openSend() {
        if (!panel) return;
        panel.showPopup();
        var client = panel.client;
        if (!client.loaded) {
            client.notice = "Still loading saved drafts. Try Send to agent again in a moment.";
        } else if (client.hasActiveDraft) {
            client.resumeDraft();
        } else {
            client.pane = "inbox";
            client.notice = "To send handwriting, close this panel, lasso it, then tap Send to agent in the selection menu. Whole-page sending is not available yet.";
        }
    }
    function openVault() {
        if (!panel) return;
        panel.showPopup();
        var client = panel.client;
        if (!client.loaded) {
            client.notice = "Still loading saved drafts. Try Send to vault again in a moment.";
        } else if (client.capture && !client.replyTo && !client.directChat) {
            client.submit(false, "obsidian");
        } else if (client.hasActiveDraft) {
            client.notice = "Only a lassoed selection can be sent to the vault. Finish or send the current draft first.";
        } else {
            client.pane = "inbox";
            client.notice = "Capture a lassoed selection with Send to agent first, then use Send to vault. Whole-page sending is not available yet.";
        }
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
