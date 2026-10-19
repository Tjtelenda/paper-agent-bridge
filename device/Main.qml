import QtQuick 2.12
import QtQuick.Controls 2.5
import QtQuick.Layouts 1.12
import ark.controls as ArkControls
import ark.tokens as ArkTokens
import GoodBot.Bridge 1.0 as GoodBot
import xofm.libs.epaper as Epaper
import "Model.js" as Model

Control {
    id: root
    font.family: "reMarkableSans"
    font.pixelSize: 24
    property alias nativeEndpoint: endpoint
    property bool panelVisible: true
    signal close
    signal sourceOpenRequested(var source)
    property var state: Model.fresh()
    property bool loaded: false
    property bool saving: false
    property bool dirty: false
    property bool syncing: false
    property string activeItemId: ""
    property bool wanted: false
    property string notice: "Loading saved inbox…"
    property var capture: null
    property var selected: null
    property var replyTo: null
    property bool directChat: false
    property string pane: "inbox"
    property string chunks: ""
    property int chunkType: 0
    property int chunkTotal: 0
    property bool closeAfterSave: false
    property bool restoring: false
    property string sourcePreview: ""
    property string unrestoredInk: ""
    property int outboxCount: 0
    property string selectedOutboxId: ""
    property var selectedOutbox: null
    property string messageText: ""
    property string selectedThreadId: ""
    property string threadDraftId: ""
    property bool threadBlocked: false
    property int chatCount: 0
    property string previewRequestId: ""
    property var confirmSource: null
    readonly property bool hasActiveDraft: capture !== null || replyTo !== null || directChat || threadDraftId.length > 0
    readonly property int keyboardInset: pane==="compose"||pane==="thread" ? Math.max(0,Qt.inputMethod.keyboardRectangle.height) : 0
    readonly property bool convertedAvailable: !replyTo&&!directChat&&capture&&typeof capture.converted_text==="string"&&capture.converted_text.trim().length>0
    // The native popup owns visibility; saved drafts survive closing it.
    function unloading() { if(draft())persist(); }
    function send(type, value) {
        var data=Qt.btoa(unescape(encodeURIComponent(String(value)))), size=32000;
        for(var at=0;at<data.length||at===0;at+=size)
            endpoint.sendMessage(100,type+"\n"+at+"\n"+data.length+"\n"+data.substr(at,size));
    }
    function persist() {
        if(!loaded)return;
        dirty=true;
        if(saving)return;
        var data=JSON.stringify(state);
        if(unescape(encodeURIComponent(data)).length>24*1024*1024){
            notice="Local storage limit reached; export state before adding captures";
            if(closeAfterSave){closeAfterSave=false;unsavedClose.open();}
            return;
        }
        saving=true;dirty=false;send(1,data);
    }
    function sync() {
        if(!loaded||syncing||saving||dirty)return;
        syncing=true;
        var item=Model.nextItem(state);
        if(item) {
            notice="Sending saved item…";activeItemId=item.id;
            send(item.route,JSON.stringify(item.payload));
        } else { notice="Checking for replies…";send(5,String(state.cursor)); }
    }
    function refresh() { wanted=true;persist(); }
    function dismissKeyboard() {
        comment.focus=false;threadComment.focus=false;converted.focus=false;root.forceActiveFocus();Qt.inputMethod.hide();
    }
    function setMessageText(value) {
        var previous=restoring;restoring=true;messageText=String(value||"");
        comment.text=messageText;threadComment.text=messageText;restoring=previous;
    }
    function draft() {
        if(restoring)return true;
        if(pane==="thread"&&selectedThreadId&&!capture&&!replyTo&&!directChat) {
            if(messageText.trim()) {threadDraftId=selectedThreadId;state.draft={threadId:selectedThreadId,comment:messageText};}
            else if(threadDraftId===selectedThreadId) {threadDraftId="";state.draft=null;}
            draftSave.restart();return true;
        }
        if(!capture&&!replyTo&&!directChat)return true;
        var drawing=unrestoredInk;
        if(!unrestoredInk&&ink.hasInk){drawing=ink.dataUrl();if(!drawing){notice=ink.error||"Drawing could not be saved; the previous draft is still kept";return false;}}
        state.draft={capture:capture,comment:messageText,converted:converted.text,destination:destination.currentIndex,
                     ink:drawing,reply:replyTo,directChat:directChat};draftSave.restart();return true;
    }
    function sourceFor(event) {
        if(!event)return null;
        return event.source||((state.captures[event.request_id||(event.message||{}).request_id]||{}).source)||null;
    }
    function outboxItem(id) {
        for(var i=0;i<state.outbox.length;i++)if(state.outbox[i].id===id)return state.outbox[i];
        return null;
    }
    function outboxDestination(item) {
        if(!item)return "";
        var payload=item.payload||{};
        return payload.destination||((item.route===4)?"Thread "+payload.request_id:"Agent delivery");
    }
    function outboxStatus(item) {
        if(!item)return "";
        if(item.status==="needs_review")return "Needs review";
        return syncing&&activeItemId===item.id?"Sending; awaiting receipt":"Queued";
    }
    function outboxText(item) {
        var content=item&&item.payload&&item.payload.content||{};
        if(content.mode==="raw_text"||content.mode==="converted_text")return content.text||"";
        return content.mode==="raw_image"?"Exact image retained below":"";
    }
    function outboxImage(item) {
        var content=item&&item.payload&&item.payload.content||{};
        return Model.imageUrl(content.image||content.source_image||null);
    }
    function outboxCommentText(item) {
        var comment=item&&item.payload&&item.payload.comment||{};
        return comment.mode==="text"?comment.text||"":"";
    }
    function outboxCommentImage(item) {
        var comment=item&&item.payload&&item.payload.comment||{};
        return comment.mode==="ink"?Model.imageUrl(comment.image):"";
    }
    function refreshViews() {
        eventList.model=Model.inboxEvents(state).slice().reverse();outboxList.model=state.outbox.slice();outboxCount=state.outbox.length;
        var chats=Model.chatThreads(state);chatList.model=chats;chatCount=chats.length;
        threadList.model=selectedThreadId?Model.threadMessages(state,selectedThreadId):[];
        threadBlocked=selectedThreadId?Model.threadPending(state,selectedThreadId):false;
        var chosen=selectedOutboxId?outboxItem(selectedOutboxId):null;selectedOutbox=null;selectedOutbox=chosen;
        if(selectedOutboxId&&!selectedOutbox)selectedOutboxId="";
        if(pane==="thread")Qt.callLater(function(){threadList.positionViewAtEnd();});
    }
    function activateTab(index) {
        if(!draft())return;dismissKeyboard();sourcePreview="";previewRequestId="";
        pane=index===0?"inbox":index===1?"chats":"outbox";if(pane==="outbox")refreshViews();
    }
    function beginChat() {
        if(hasActiveDraft){notice="Finish or send the current draft first";return;}
        restoring=true;directChat=true;destination.currentIndex=0;setMessageText("");converted.text="";
        unrestoredInk="";ink.clear();sourcePreview="";pane="compose";restoring=false;draft();
    }
    function resumeDraft() {
        if(threadDraftId){openThread(threadDraftId);return;}
        if(capture||replyTo||directChat)pane="compose";
    }
    function threadInfo(id) {
        var chats=Model.chatThreads(state);for(var i=0;i<chats.length;i++)if(chats[i].request_id===id)return chats[i];return null;
    }
    function requestPreview(id) {
        if(!id||syncing)return;sourcePreview="";previewRequestId=id;syncing=true;send(7,id);
    }
    function openThread(id) {
        if(!id)return;
        if(capture||replyTo||directChat||(threadDraftId&&threadDraftId!==id)) {notice="Finish or send the current draft before opening another chat";return;}
        selectedThreadId=id;pane="thread";if(threadDraftId!==id)setMessageText("");refreshViews();
        var info=threadInfo(id),cached=state.captures[id];
        if(info&&info.source&&(!cached||!cached.image))requestPreview(id);
    }
    function submitThread() {
        try {
            var target=Model.replyTarget(state,selectedThreadId);
            if(!target)throw Error("This chat has no accepted message to reply to yet");
            if(Model.threadPending(state,selectedThreadId))throw Error("Wait for the queued message before replying again");
            Model.queueReply(state,target,messageText,"");threadDraftId="";state.draft=null;setMessageText("");
            wanted=true;dismissKeyboard();refreshViews();persist();notice="Saving reply to Outbox…";
        } catch(e){notice=String(e);}
    }
    function openSource(source) {if(!source)return;confirmSource=source;openConfirm.open();}
    function retryOutbox(id) {
        var item=outboxItem(id),next=Model.nextItem(state);
        if(!loaded||saving||dirty||syncing||!item||item.status==="needs_review"||!next||next.id!==id){
            notice="Only the next queued item can be retried safely";return;
        }
        syncing=true;activeItemId=item.id;notice="Retrying saved item…";send(item.route,JSON.stringify(item.payload));
    }
    function deliveryNotice(message, delivery) {
        var text=String(message);
        var privateConfig=/(?:config|configuration).*(?:owned by|mode\s*600|missing|invalid|permission|not configured)|(?:owned by|mode\s*600).*(?:config|configuration)/i.test(text);
        if(privateConfig||(delivery&&/(certificate|tls|ssl|http|network|connect|offline|timed? out|timeout)/i.test(text)))
            return "Agent delivery is not connected yet. Messages are saved in Outbox.";
        return text;
    }
    function submit(asText) {
        try {
            var image="";
            if(unrestoredInk)throw Error("The saved drawing could not be restored. Clear it explicitly before sending.");
            if(ink.hasInk){image=ink.dataUrl();if(!image)throw Error(ink.error||"Drawing could not be saved");}
            if(image&&messageText.trim()){notice="Choose typed OR drawn comment; clear one before sending";return;}
            if(directChat)Model.queueConversation(state,destination.currentText.toLowerCase(),messageText);
            else if(replyTo)Model.queueReply(state,replyTo,messageText,image);
            else Model.queue(state,capture,destination.currentText.toLowerCase(),asText?converted.text:null,messageText,image);
            capture=null;replyTo=null;directChat=false;unrestoredInk="";state.draft=null;setMessageText("");pane="chats";wanted=true;dismissKeyboard();refreshViews();persist();notice="Saving outbox…";
        } catch(e){notice=String(e);}
    }
    function receive(type, body) {
        try {
            if(type===100) {
                var a=body.indexOf('\n'),b=body.indexOf('\n',a+1),c=body.indexOf('\n',b+1);
                var t=Number(body.slice(0,a)),offset=Number(body.slice(a+1,b)),total=Number(body.slice(b+1,c));
                if(offset===0){chunks="";chunkType=t;chunkTotal=total;}
                if(offset!==chunks.length||t!==chunkType||total!==chunkTotal||total>32*1024*1024)throw Error('Invalid IPC chunk');
                chunks+=body.slice(c+1);if(chunks.length<total)return;
                if(chunks.length!==total)throw Error('IPC length mismatch');
                type=chunkType;body=decodeURIComponent(escape(Qt.atob(chunks)));chunks="";
            }
            if(type===0) {
                if(loaded)return;restoring=true;state=Model.restore(JSON.parse(body));notice="Saved inbox restored";
                if(state.draft&&state.draft.threadId){threadDraftId=state.draft.threadId;selectedThreadId=threadDraftId;setMessageText(state.draft.comment||"");pane="thread";
                } else if(state.draft){directChat=!!state.draft.directChat;capture=state.draft.capture;setMessageText(state.draft.comment||"");converted.text=state.draft.converted||"";
                    destination.currentIndex=state.draft.destination||0;replyTo=state.draft.reply||null;pane="compose";
                    if(state.draft.ink&&!ink.loadDataUrl(state.draft.ink)){unrestoredInk=state.draft.ink;notice=ink.error||"Saved drawing could not be restored; the original drawing is still retained";}}
                restoring=false;loaded=true;
            } else if(type===1) {
                saving=false;if(dirty){persist();return;}
                if(closeAfterSave){closeAfterSave=false;root.close();return;}
                if(wanted){wanted=false;sync();}
            } else if(type===2) {
                var handoff=JSON.parse(body);
                if(!handoff.source||!handoff.image)throw Error('Capture needs source and image');
                if(state.draft||capture||replyTo||directChat){notice="Finish or send the current draft before starting another selection";refreshViews();return;}
                restoring=true;directChat=false;capture=handoff;converted.text=handoff.converted_text||"";setMessageText("");unrestoredInk="";ink.clear();pane="compose";restoring=false;draft();
            } else if(type===3||type===4||type===10) {
                Model.accept(state,JSON.parse(body));activeItemId="";syncing=false;wanted=true;persist();
            } else if(type===5) {
                var response=JSON.parse(body);Model.ingest(state,response);syncing=false;
                notice=response.events.length?"New replies or status updates received":"Inbox up to date";
                wanted=response.has_more;persist();
            } else if(type===7) {syncing=false;sourcePreview=body;
            } else if(type===8) {
                syncing=false;if(activeItemId){Model.reject(state,activeItemId,body);activeItemId="";wanted=true;persist();}notice=deliveryNotice(body,true);
            } else if(type===9) {
                var wasSyncing=syncing,sendFailed=activeItemId!=="";activeItemId="";syncing=false;notice=body;
                notice=deliveryNotice(body,sendFailed||wasSyncing);
                // A deferred send must not starve incoming replies; retry its same ID next cycle.
                if(sendFailed){syncing=true;send(5,String(state.cursor));}
            } else if(type===11) {
                saving=false;dirty=true;notice=body;
                if(closeAfterSave){closeAfterSave=false;unsavedClose.open();}
            } else if(type===12) {
                if(saving)dirty=true;saving=false;syncing=false;activeItemId="";
                notice="Agent service is unavailable. Your current draft and Outbox remain open.";
                if(closeAfterSave){closeAfterSave=false;unsavedClose.open();}
            }
            refreshViews();
        }catch(e){restoring=false;syncing=false;saving=false;notice=String(e);}
    }
    GoodBot.Endpoint {
        id: endpoint
        applicationID: "goodbot.bridge"
        onMessageReceived: function(type, contents) { root.receive(type, contents); }
    }
    Component.onCompleted: send(0,"")
    Timer {id:draftSave;interval:400;onTriggered:persist()}
    Timer { interval:30000;running:loaded&&panelVisible;repeat:true;onTriggered:refresh() }
    Rectangle { anchors.fill:parent;color:"white" }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Label { text: "Agent"; font.pixelSize: 28; Layout.fillWidth: true }
            ArkControls.Button { text: "Hide keyboard"; type: ArkTokens.Button.tertiary; enabled: root.keyboardInset > 0; onClicked: dismissKeyboard() }
            ArkControls.Button { text: "Refresh"; type: ArkTokens.Button.tertiary; enabled: loaded && !syncing; onClicked: refresh() }
            ArkControls.Button {
                text: "Close"; type: ArkTokens.Button.tertiary; enabled: !saving
                onClicked: {
                    if (!loaded) { root.close(); return; }
                    if (!draft()) return;
                    dismissKeyboard(); closeAfterSave = true; persist();
                }
            }
        }

        Label { text: notice; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 18 }

        Item {
            id: tabBar
            Layout.fillWidth: true
            Layout.preferredHeight: 58
            property int currentIndex: pane === "outbox" ? 2 : (pane === "chats" || pane === "thread" || (pane === "compose" && directChat) ? 1 : 0)
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 2; color: "black" }
            Row {
                anchors.fill: parent
                Repeater {
                    model: ["Inbox", "Chats" + (chatCount ? " (" + chatCount + ")" : ""), "Outbox" + (outboxCount ? " (" + outboxCount + ")" : "")]
                    delegate: Item {
                        width: tabBar.width / 3; height: tabBar.height
                        Label { anchors.fill: parent; anchors.bottomMargin: 5; text: modelData; color: "black"; font.pixelSize: 22; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 6; color: index === tabBar.currentIndex ? "black" : "white" }
                        MouseArea { anchors.fill: parent; onClicked: root.activateTab(index) }
                    }
                }
            }
        }

        Label { visible: pane === "inbox"; text: "Updates and receipts"; font.pixelSize: 22 }
        Label { visible: pane === "inbox" && eventList.count === 0; text: "Inbox is empty"; font.pixelSize: 20 }
        ListView {
            id: eventList; visible: pane === "inbox"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10; model: []
            delegate: Rectangle {
                width: eventList.width; height: eventLabel.implicitHeight + 32; color: "white"; border.color: "#888888"
                Label { id: eventLabel; anchors.fill: parent; anchors.margins: 16; text: Model.summary(modelData); wrapMode: Text.Wrap; font.pixelSize: 20 }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        selected = modelData; sourcePreview = ""; previewRequestId = ""; pane = "response";
                        var rid = selected.request_id || (selected.message || {}).request_id;
                        if (sourceFor(selected) && rid && (!root.state.captures[rid] || !root.state.captures[rid].image)) requestPreview(rid);
                    }
                }
            }
        }

        Item {
            visible: pane === "chats"; Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "Conversations"; font.pixelSize: 22; Layout.fillWidth: true }
                    ArkControls.Button { text: "Resume draft"; type: ArkTokens.Button.tertiary; visible: root.hasActiveDraft; onClicked: resumeDraft() }
                    ArkControls.Button { text: "New chat"; type: ArkTokens.Button.secondary; enabled: loaded && !saving && !root.hasActiveDraft; onClicked: beginChat() }
                }
                Label { visible: chatList.count === 0; text: "No conversations yet"; font.pixelSize: 20 }
                ListView {
                    id: chatList; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10; model: []
                    delegate: Rectangle {
                        width: chatList.width; height: chatRow.implicitHeight + 30; color: "white"; border.color: "#888888"
                        ColumnLayout {
                            id: chatRow; anchors.fill: parent; anchors.margins: 15; spacing: 4
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: String(modelData.destination || "Agent").replace(/^./, function(c){ return c.toUpperCase(); }); font.pixelSize: 22; Layout.fillWidth: true }
                                Label { visible: modelData.pending; text: "Queued"; font.pixelSize: 17 }
                            }
                            Label { text: modelData.preview; elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 19 }
                            Label { text: modelData.turns + (modelData.turns === 1 ? " turn" : " turns"); font.pixelSize: 16 }
                        }
                        MouseArea { anchors.fill: parent; onClicked: root.openThread(modelData.request_id) }
                    }
                }
            }
        }

        Item {
            visible: pane === "thread"; Layout.fillWidth: true; Layout.fillHeight: true; Layout.bottomMargin: root.keyboardInset
            ColumnLayout {
                anchors.fill: parent; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    ArkControls.Button { text: "Back"; type: ArkTokens.Button.tertiary; onClicked: { if (draft()) { dismissKeyboard(); pane = "chats"; } } }
                    Label { text: (threadInfo(selectedThreadId) || {}).destination || "Conversation"; font.pixelSize: 23; Layout.fillWidth: true }
                }
                ListView {
                    id: threadList; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8; model: []
                    delegate: Item {
                        id: turn; width: threadList.width; height: turnCard.height + 4
                        property string shownImage: modelData.image || (modelData.preview_request_id === root.previewRequestId ? root.sourcePreview : "")
                        Rectangle {
                            id: turnCard; width: turn.width * 0.84; height: turnBody.implicitHeight + 28
                            x: modelData.role === "user" ? turn.width - width : 0
                            color: modelData.role === "user" ? "#eeeeee" : "white"; border.color: "#777777"
                            ColumnLayout {
                                id: turnBody; anchors.fill: parent; anchors.margins: 14; spacing: 5
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: modelData.actor; font.bold: true; font.pixelSize: 18; Layout.fillWidth: true }
                                    Label { visible: !!modelData.status; text: modelData.status; font.pixelSize: 16 }
                                }
                                Label { text: modelData.text; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 20 }
                                Image {
                                    visible: turn.shownImage.length > 0; source: turn.shownImage; Layout.fillWidth: true; Layout.preferredHeight: visible ? 180 : 0; fillMode: Image.PreserveAspectFit
                                    MouseArea { anchors.fill: parent; enabled: !!modelData.source; onClicked: root.openSource(modelData.source) }
                                }
                                Image { visible: !!modelData.comment_image; source: modelData.comment_image || ""; Layout.fillWidth: true; Layout.preferredHeight: visible ? 140 : 0; fillMode: Image.PreserveAspectFit }
                            }
                        }
                    }
                }
                Label { visible: threadBlocked; text: "A message is queued in Outbox. Send another after it is accepted."; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 17 }
                RowLayout {
                    Layout.fillWidth: true
                    TextArea {
                        id: threadComment; text: root.messageText; enabled: !threadBlocked; placeholderText: "Reply to this conversation"; wrapMode: Text.Wrap
                        Layout.fillWidth: true; Layout.preferredHeight: 92
                        onTextChanged: { if (root.messageText !== text) root.messageText = text; if (loaded && !restoring) draft(); }
                    }
                    ArkControls.Button { text: "Send"; type: ArkTokens.Button.primary; enabled: loaded && !saving && !threadBlocked && messageText.trim().length > 0; onClicked: submitThread() }
                }
            }
        }

        Label { visible: pane === "outbox" && !selectedOutbox && outboxCount === 0; text: "Outbox is empty"; font.pixelSize: 22 }
        ListView {
            id: outboxList; visible: pane === "outbox" && !selectedOutbox; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10; model: []
            delegate: Rectangle {
                width: outboxList.width; height: outboxRow.implicitHeight + 32; color: "white"; border.color: "#888888"
                ColumnLayout { id: outboxRow; anchors.fill: parent; anchors.margins: 16
                    Label { text: root.outboxStatus(modelData) + " · " + root.outboxDestination(modelData); font.pixelSize: 22; Layout.fillWidth: true }
                    Label { text: "Stable ID: " + modelData.id; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 17 }
                }
                MouseArea { anchors.fill: parent; onClicked: { root.selectedOutboxId = modelData.id; root.selectedOutbox = root.outboxItem(modelData.id); } }
            }
        }

        Item {
            visible: pane === "compose"; Layout.fillWidth: true; Layout.fillHeight: true; Layout.bottomMargin: root.keyboardInset
            ColumnLayout {
                anchors.fill: parent; spacing: 12
                ScrollView {
                    id: composeScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    ColumnLayout {
                        width: composeScroll.availableWidth; spacing: 14
                        Label { text: directChat ? "Start an agent conversation" : replyTo ? "Confirm threaded reply" : "Confirm original selection"; font.pixelSize: 24 }
                        Image { visible: !directChat && (!!capture || sourcePreview.length > 0); source: capture && capture.image ? Model.imageUrl(capture.image) : sourcePreview; Layout.fillWidth: true; Layout.preferredHeight: 240; fillMode: Image.PreserveAspectFit }
                        Label { text: capture ? Model.sourceLabel(capture.source) : ""; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        ComboBox { id: destination; visible: !replyTo; model: directChat ? ["Hermes", "Muse", "Codex"] : ["Hermes", "Muse", "Codex", "Obsidian"]; enabled: !replyTo; onActivated: draft() }
                        Label { visible: root.convertedAvailable; text: "Converted text from this capture"; font.pixelSize: 20 }
                        TextArea { id: converted; visible: root.convertedAvailable; placeholderText: "Review converted text"; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredHeight: 150; onTextChanged: if (loaded) draft() }
                        Label { visible: !replyTo && !directChat && !root.convertedAvailable; text: "Text conversion is unavailable for this selection. The original image can still be sent."; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        TextArea {
                            id: comment; text: root.messageText; placeholderText: directChat ? "Type your message" : replyTo ? "Type your reply" : "Optional typed comment"; wrapMode: Text.Wrap
                            Layout.fillWidth: true; Layout.preferredHeight: 150
                            onTextChanged: { if (root.messageText !== text) root.messageText = text; if (loaded && !restoring) draft(); }
                        }
                        Label { visible: !directChat; text: replyTo ? "Or draw your reply below" : "Or draw a comment below" }
                        Label { visible: unrestoredInk.length > 0; text: "The saved drawing could not be displayed, but it is still retained in this draft. Clear it explicitly before sending."; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Item {
                            id: inkFrame; visible: !directChat; Layout.fillWidth: true; Layout.preferredHeight: 220
                            Rectangle { anchors.fill: parent; color: "white"; border.color: "#888888" }
                            Epaper.ScreenModeItem { anchors.fill: parent; mode: Epaper.ScreenModeItem.Pen; visible: inkFrame.visible && root.panelVisible }
                            GoodBot.InkPad { id: ink; anchors.fill: parent; enabled: unrestoredInk.length === 0; onChanged: if (root.loaded && !root.restoring) root.draft(); onErrorChanged: if (error) root.notice = error }
                        }
                        ArkControls.Button { visible: unrestoredInk.length > 0 || (!directChat && ink.hasInk); text: "Clear drawing"; type: ArkTokens.Button.tertiary; onClicked: { unrestoredInk = ""; ink.clear(); draft(); } }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ArkControls.Button { text: directChat ? "Send message" : replyTo ? "Send reply" : "Send original image"; type: ArkTokens.Button.primary; enabled: loaded && !saving; onClicked: submit(false) }
                    ArkControls.Button { text: "Send converted text"; type: ArkTokens.Button.secondary; visible: root.convertedAvailable; enabled: loaded && !saving && converted.text.trim().length > 0; onClicked: submit(true) }
                }
            }
        }

        Item {
            visible: pane === "outbox" && !!selectedOutbox; Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout { anchors.fill: parent; spacing: 12
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    ColumnLayout { width: parent.width; spacing: 12
                        Label { text: selectedOutbox ? root.outboxStatus(selectedOutbox) + " · " + root.outboxDestination(selectedOutbox) : ""; font.pixelSize: 24; Layout.fillWidth: true }
                        Label { text: selectedOutbox ? "Stable ID: " + selectedOutbox.id : ""; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 18 }
                        Label { visible: selectedOutbox && !!selectedOutbox.reason; text: "Last error: " + (selectedOutbox ? selectedOutbox.reason : ""); wrapMode: Text.Wrap; Layout.fillWidth: true }
                        TextArea { visible: selectedOutbox && root.outboxText(selectedOutbox).length > 0; text: selectedOutbox ? root.outboxText(selectedOutbox) : ""; readOnly: true; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredHeight: 180 }
                        Image { visible: selectedOutbox && root.outboxImage(selectedOutbox).length > 0; source: selectedOutbox ? root.outboxImage(selectedOutbox) : ""; Layout.fillWidth: true; Layout.preferredHeight: 260; fillMode: Image.PreserveAspectFit }
                        Label { visible: selectedOutbox && root.outboxCommentText(selectedOutbox).length > 0; text: "Comment"; font.pixelSize: 20 }
                        TextArea { visible: selectedOutbox && root.outboxCommentText(selectedOutbox).length > 0; text: selectedOutbox ? root.outboxCommentText(selectedOutbox) : ""; readOnly: true; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredHeight: 140 }
                        Image { visible: selectedOutbox && root.outboxCommentImage(selectedOutbox).length > 0; source: selectedOutbox ? root.outboxCommentImage(selectedOutbox) : ""; Layout.fillWidth: true; Layout.preferredHeight: 220; fillMode: Image.PreserveAspectFit }
                    }
                }
                RowLayout { Layout.fillWidth: true
                    ArkControls.Button { text: "Back to Outbox"; type: ArkTokens.Button.tertiary; onClicked: { selectedOutboxId = ""; selectedOutbox = null; } }
                    ArkControls.Button { text: "Retry queued item"; type: ArkTokens.Button.secondary; visible: selectedOutbox && selectedOutbox.status !== "needs_review"; enabled: selectedOutbox && Model.nextItem(root.state) && Model.nextItem(root.state).id === selectedOutbox.id && !saving && !dirty && !syncing; onClicked: retryOutbox(selectedOutbox.id) }
                }
            }
        }

        ScrollView {
            visible: pane === "response"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            ColumnLayout {
                width: root.width - 60; spacing: 16
                Image {
                    visible: !!sourceFor(selected); Layout.fillWidth: true; Layout.preferredHeight: 240; fillMode: Image.PreserveAspectFit
                    source: selected && root.state.captures[selected.request_id || (selected.message || {}).request_id] && root.state.captures[selected.request_id || (selected.message || {}).request_id].image ? Model.imageUrl(root.state.captures[selected.request_id || (selected.message || {}).request_id].image) : sourcePreview
                    MouseArea { anchors.fill: parent; onClicked: root.openSource(root.sourceFor(selected)) }
                }
                Label { text: selected ? Model.summary(selected) : ""; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 22 }
                ArkControls.Button { visible: !!sourceFor(selected); text: "Open exact source…"; type: ArkTokens.Button.secondary; onClicked: root.openSource(root.sourceFor(selected)) }
                ArkControls.Button { text: "Back to Inbox"; type: ArkTokens.Button.tertiary; onClicked: pane = "inbox" }
            }
        }
    }
    Dialog {
        id: openConfirm; title: "Open the current source page?"; modal: true; standardButtons: Dialog.Ok | Dialog.Cancel
        Label { text: Model.sourceLabel(root.confirmSource) + "\nThe page may have changed since this capture."; wrapMode: Text.Wrap; width: Math.min(root.width - 100, 500) }
        onAccepted: root.sourceOpenRequested(root.confirmSource)
    }
    Dialog {
        id: unsavedClose; title: "Close with an unsaved draft?"; modal: true; standardButtons: Dialog.Ok | Dialog.Cancel
        Label { text: "Saving failed. Closing keeps the draft in memory while the notebook app stays open. Restarting may lose these changes."; wrapMode: Text.Wrap; width: Math.min(root.width - 100, 500) }
        onAccepted: root.close()
    }
}
