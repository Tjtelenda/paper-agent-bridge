pragma Singleton
import QtQuick
import "qrc:/goodbot/Model.js" as Model

QtObject {
    property QtObject notebookControl: NotebookControl {}
    property var panel: null
    property bool capturingPage: false
    signal vaultStatus(string message)
    function pageLayout(paper, content) { return Model.pageLayout(paper, content); }
    function sourcePageIndex(source, entry) { return Model.sourcePageIndex(source, entry); }
    function openInbox() {
        if (!panel) return;
        panel.showPopup();
        panel.client.pane = "queued";
        console.log("GOODBOT_INBOX_OPENED");
    }
    function openSend(provider) {
        if (!panel) return;
        var client = panel.client;
        if (!client.loaded) {
            client.notice = "Still loading saved drafts. Try Send to agent again in a moment.";
        } else if (client.hasActiveDraft) {
            client.resumeDraft();
        } else {
            requestPage(provider, "agent");
        }
        panel.showPopup();
    }
    function openVault(provider) {
        if (!panel) return;
        if (!panel.client.loaded) { vaultStatus("Still loading saved drafts; try Send to vault again in a moment"); return; }
        requestPage(provider, "obsidian");
    }
    function requestPage(provider, destination) {
        if (!panel || capturingPage) return;
        var client=panel.client;
        if(!client.loaded || (destination!=="obsidian" && client.hasActiveDraft))return;
        if(!provider || typeof provider.goodbotCapturePage!=="function") {
            client.notice="The current page is not ready to capture.";
            if(destination==="obsidian")vaultStatus(client.notice);return;
        }
        capturingPage=true;
        if(destination!=="obsidian"){client.pane="queued";client.notice="Rendering the whole page…";}
        var done=false;
        function fail(reason) {
            if(done)return;done=true;capturingPage=false;client.notice=String(reason);
            if(destination==="obsidian")vaultStatus(client.notice);
        }
        try {
            provider.goodbotCapturePage(function(image,notebook,page,title,label,rect,bounds) {
                if(done)return;
                try {
                    if(destination!=="obsidian" && client.hasActiveDraft)throw Error("Your existing draft was preserved; finish it before capturing a page");
                    var encoded=client.nativeEndpoint.encodeImage(image);
                    var handoff=rect ? Model.selectionCapture(encoded,notebook,page,title,label,rect,bounds)
                        : Model.pageCapture(encoded,notebook,page,title,label);
                    if(destination==="obsidian")client.queueVault(handoff);
                    else {
                        client.receive(2,JSON.stringify(handoff));
                        if(!client.capture || client.capture.source.page_id!==page)
                            throw Error("The captured page could not be opened as a draft");
                    }
                    done=true;capturingPage=false;
                    console.log("GOODBOT_PAGE_CAPTURED");
                } catch(error) { fail(error); }
            },fail);
        } catch(error) { fail(error); }
    }
    function capture(image, notebook, page, title, label, rect, bounds, destination) {
        if (!panel) return;
        var client = panel.client;
        if (destination !== "obsidian") panel.showPopup();
        if (capturingPage) {
            client.notice = "Wait for the current page capture to finish.";
            if(destination === "obsidian")vaultStatus(client.notice);
            return;
        }
        if (!client.loaded || (destination !== "obsidian" && client.hasActiveDraft)) {
            client.notice = "Finish or send your current draft before capturing another selection.";
            if(destination === "obsidian")vaultStatus(client.notice);
            return;
        }
        try {
            var handoff = Model.selectionCapture(client.nativeEndpoint.encodeImage(image),
                notebook, page, title, label, rect, bounds);
            if (destination === "obsidian") client.queueVault(handoff);
            else {
                client.receive(2, JSON.stringify(handoff));
                if (!client.capture || client.capture.source.notebook_id !== notebook
                        || client.capture.source.page_id !== page
                        || client.capture.source.revision !== handoff.source.revision
                        || client.capture.source.selection !== "crop")
                    throw Error("The captured selection could not be opened as a draft");
            }
            console.log("GOODBOT_SELECTION_CAPTURED");
        } catch (error) {
            client.notice = String(error);
            if(destination === "obsidian")vaultStatus(client.notice);
        }
    }
}
