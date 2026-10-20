import QtQuick 2.12
import QtQuick.Controls 2.5
import QtQuick.Layouts 1.12
import ark.controls as ArkControls
import ark.tokens as ArkTokens
import device.ui.controls as DeviceControls
import GoodBot.Bridge 1.0 as GoodBot
import xofm.libs.epaper as Epaper
import xofm.libs.dialogs as Dialogs
import "Model.js" as Model

Control {
    id: root
    enabled: !GoodBot.Session.capturingPage
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
    property bool vaultSavePending: false
    property bool syncing: false
    property string activeItemId: ""
    property bool wanted: false
    property string notice: "Loading saved submissions…"
    property var capture: null
    property string pane: "queued"
    property string chunks: ""
    property int chunkType: 0
    property int chunkTotal: 0
    property bool closeAfterSave: false
    property bool restoring: false
    property string sourcePreview: ""
    property string unrestoredInk: ""
    property int queuedCount: 0
    property int historyCount: 0
    property string selectedSubmissionId: ""
    property var selectedSubmission: null
    property bool selectedQueued: false
    property string messageText: ""
    property string previewRequestId: ""
    property var confirmSource: null
    readonly property int activeTabIndex: pane === "history" ? 1 : 0
    readonly property bool hasActiveDraft: state.draft !== null
    readonly property int keyboardInset: pane==="compose" ? Math.max(0,Qt.inputMethod.keyboardRectangle.height) : 0
    readonly property bool convertedAvailable: capture&&typeof capture.converted_text==="string"&&capture.converted_text.trim().length>0
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
            if(vaultSavePending)GoodBot.Session.vaultStatus("Vault item is not saved; open Agent submissions to retry saving");
            if(closeAfterSave){closeAfterSave=false;unsavedClose.visible=true;}
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
        } else { notice="Checking submission status…";send(5,String(state.cursor)); }
    }
    function refresh() { wanted=true;persist(); }
    function dismissKeyboard() {
        comment.focus=false;converted.focus=false;root.forceActiveFocus();Qt.inputMethod.hide();
    }
    function typingFieldContains(position) {
        function contains(item) {
            if(!item.visible)return false;
            var local=item.mapFromItem(root,position.x,position.y);
            return item.contains(Qt.point(local.x,local.y));
        }
        return contains(comment)||contains(converted);
    }
    function setMessageText(value) {
        var previous=restoring;restoring=true;messageText=String(value||"");
        comment.text=messageText;restoring=previous;
    }
    function draft() {
        if(restoring)return true;
        if(!capture)return true;
        if(state.draft===null&&pane!=="compose")return true;
        var drawing=unrestoredInk;
        if(!unrestoredInk&&ink.hasInk){drawing=ink.dataUrl();if(!drawing){notice=ink.error||"Drawing could not be saved; the previous draft is still kept";return false;}}
        state.draft={capture:capture,comment:messageText,converted:converted.text,destination:destination.currentIndex,
                     ink:drawing};draftSave.restart();return true;
    }
    function outboxItem(id) {
        for(var i=0;i<state.outbox.length;i++)if(state.outbox[i].id===id)return state.outbox[i];
        return null;
    }
    function outboxDestination(item) {
        if(!item)return "";
        var payload=item.payload||{};
        return payload.destination||((item.route===4)?"Legacy agent reply":"Agent delivery");
    }
    function outboxStatus(item) {
        if(!item)return "";
        if(item.status==="needs_review")return "Needs review";
        if(item.payload&&item.payload.destination==="obsidian")return "Queued for vault; awaiting relay receipt";
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
    function historyText(item) {
        var content=item&&item.content||{};
        if(content.mode==="raw_text")return content.text||"";
        if(content.mode==="converted_text")return content.text||content.converted_text||"";
        return content.mode==="raw_image"?(item.source?"Original image submission":"Handwritten legacy submission"):"";
    }
    function historyImage(item) {
        if(!item)return "";
        var saved=state.captures[item.request_id]||{},content=item.content||{};
        return Model.imageUrl(saved.image||content.image||content.source_image||null)||
            (previewRequestId===item.request_id?sourcePreview:"");
    }
    function historyComment(item) {
        var value=item&&item.comment||{};return value.mode==="text"?value.text||"":value.mode==="ink"?"Handwritten comment":"";
    }
    function refreshViews() {
        var history=Model.historySubmissions(state);
        queuedList.model=state.outbox.slice();queuedCount=state.outbox.length;
        historyList.model=history;historyCount=history.length;
        if(selectedSubmissionId) {
            var chosen=selectedQueued?outboxItem(selectedSubmissionId):null;
            if(!chosen)for(var i=0;i<history.length;i++)
                if(history[i].id===selectedSubmissionId){chosen=history[i];selectedQueued=false;break;}
            selectedSubmission=null;selectedSubmission=chosen;
            if(!chosen){selectedSubmissionId="";pane=selectedQueued?"queued":"history";}
        }
    }
    function activateTab(index) {
        if(!draft())return;dismissKeyboard();sourcePreview="";previewRequestId="";
        selectedSubmissionId="";selectedSubmission=null;pane=index===0?"queued":"history";refreshViews();
    }
    function resumeDraft() {
        if(capture)pane="compose";
    }
    function requestPreview(id) {
        if(!id||syncing)return;sourcePreview="";previewRequestId=id;syncing=true;send(7,id);
    }
    function openSource(source) {if(!source)return;dismissKeyboard();confirmSource=source;openConfirm.visible=true;}
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
            return "Agent delivery is not connected yet. Submissions remain in Queued.";
        return text;
    }
    function queueVault(handoff) {
        if(!loaded)throw Error("Still loading saved drafts; try again in a moment");
        if(vaultSavePending)throw Error("Wait for the vault item to finish saving");
        if(!draft())throw Error("The current draft could not be saved; nothing was queued");
        Model.queue(state,handoff,"obsidian",null,"","",true);
        vaultSavePending=true;wanted=true;refreshViews();persist();
    }
    function submit(asText) {
        try {
            var image="";
            if(unrestoredInk)throw Error("The saved drawing could not be restored. Clear it explicitly before sending.");
            if(ink.hasInk){image=ink.dataUrl();if(!image)throw Error(ink.error||"Drawing could not be saved");}
            if(image&&messageText.trim()){notice="Choose typed OR drawn comment; clear one before sending";return;}
            Model.queue(state,capture,destination.currentText.toLowerCase(),asText?converted.text:null,messageText,image);
            var wasRestoring=restoring;restoring=true;state.draft=null;pane="queued";unrestoredInk="";ink.clear();setMessageText("");restoring=wasRestoring;
            wanted=true;dismissKeyboard();refreshViews();
            notice="Saving queued submission…";persist();
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
                if(loaded)return;restoring=true;state=Model.restore(JSON.parse(body));notice="Saved submissions restored";
                if(state.draft&&state.draft.capture){capture=state.draft.capture;setMessageText(state.draft.comment||"");converted.text=state.draft.converted||"";
                    destination.currentIndex=state.draft.destination||0;pane="compose";
                    if(state.draft.ink&&!ink.loadDataUrl(state.draft.ink)){unrestoredInk=state.draft.ink;notice=ink.error||"Saved drawing could not be restored; the original drawing is still retained";}}
                else if(state.draft)notice="A legacy conversation draft is retained in saved state. Use your normal agent channel for new messages.";
                else capture=state.last_capture;
                restoring=false;loaded=true;
            } else if(type===1) {
                saving=false;if(dirty){persist();return;}
                if(vaultSavePending){vaultSavePending=false;GoodBot.Session.vaultStatus("Queued locally for vault");}
                if(closeAfterSave){closeAfterSave=false;root.close();return;}
                if(wanted){wanted=false;sync();}
            } else if(type===2) {
                var handoff=JSON.parse(body);
                if(!handoff.source||!handoff.image)throw Error('Capture needs source and image');
                if(state.draft){notice="Finish or send the current draft before starting another selection";refreshViews();return;}
                restoring=true;capture=handoff;converted.text=handoff.converted_text||"";setMessageText("");
                unrestoredInk="";ink.clear();destination.currentIndex=1;pane="compose";restoring=false;draft();
            } else if(type===3||type===4||type===10) {
                Model.accept(state,JSON.parse(body));activeItemId="";syncing=false;wanted=true;persist();
            } else if(type===5) {
                var response=JSON.parse(body);Model.ingest(state,response);syncing=false;
                notice=response.events.length?"Submission history updated":"Submissions up to date";
                wanted=response.has_more;persist();
            } else if(type===7) {syncing=false;sourcePreview=body;
            } else if(type===8) {
                syncing=false;if(activeItemId){Model.reject(state,activeItemId,body);activeItemId="";wanted=true;persist();}notice=deliveryNotice(body,true);
            } else if(type===9) {
                var wasSyncing=syncing,sendFailed=activeItemId!=="";activeItemId="";syncing=false;notice=body;
                notice=deliveryNotice(body,sendFailed||wasSyncing);
                // A deferred send must not starve incoming status; retry its same ID next cycle.
                if(sendFailed){syncing=true;send(5,String(state.cursor));}
            } else if(type===11) {
                saving=false;dirty=true;notice=body;
                if(vaultSavePending)GoodBot.Session.vaultStatus("Vault item is not saved; open Agent submissions to retry saving");
                if(closeAfterSave){closeAfterSave=false;unsavedClose.visible=true;}
            } else if(type===12) {
                if(saving)dirty=true;saving=false;syncing=false;activeItemId="";
                notice="Agent service is unavailable. Your current draft and queued submissions remain open.";
                if(vaultSavePending)GoodBot.Session.vaultStatus("Vault item is not saved; open Agent submissions to retry saving");
                if(closeAfterSave){closeAfterSave=false;unsavedClose.visible=true;}
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
    onActiveTabIndexChanged: tabs.currentIndex = activeTabIndex
    Rectangle { anchors.fill:parent;color:"white" }
    TapHandler {
        gesturePolicy: TapHandler.DragThreshold
        onTapped: function(eventPoint) { if(!root.typingFieldContains(eventPoint.position))root.dismissKeyboard(); }
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 12

        ArkControls.NavigationBar {
            Layout.fillWidth: true
            type: ArkTokens.NavigationBar.secondary
            title: "Agent"
            borderPosition: ArkControls.NavigationBar.Border.Bottom

            Item { Layout.fillWidth: true }
            ArkControls.NavigationBar.Button { text: "Refresh"; enabled: loaded && !syncing; onClicked: refresh() }
            ArkControls.NavigationBar.Button {
                text: "Close"; type: ArkTokens.Button.tertiary; enabled: !saving
                onClicked: {
                    if (!loaded) { root.close(); return; }
                    if (!draft()) return;
                    dismissKeyboard(); closeAfterSave = true; persist();
                }
            }
        }

        ArkControls.Body { text: notice; type: ArkControls.Body.Small; wrapMode: Text.Wrap; Layout.fillWidth: true }

        DeviceControls.TabControl {
            id: tabs
            Layout.fillWidth: true
            model: ["Queued" + (queuedCount ? " (" + queuedCount + ")" : ""), "History" + (historyCount ? " (" + historyCount + ")" : "")]
            currentIndex: root.activeTabIndex
            onCurrentIndexChanged: {
                if (currentIndex === root.activeTabIndex) return;
                root.activateTab(currentIndex);
                currentIndex = root.activeTabIndex;
            }
        }

        Item {
            visible: pane === "queued"; Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    ArkControls.Title { text: "Queued submissions"; type: ArkControls.Title.Medium; Layout.fillWidth: true }
                    ArkControls.Button { text: "Resume draft"; type: ArkTokens.Button.tertiary; visible: root.hasActiveDraft; onClicked: resumeDraft() }
                }
                ArkControls.Body { visible: queuedList.count === 0 && !state.draft && !capture; text: "Nothing is waiting to be submitted" }
                ArkControls.Body { visible: !!state.draft && !capture; text: "A legacy conversation draft is retained. Continue agent conversations through your normal channels."; type: ArkControls.Body.Small; wrapMode: Text.Wrap; Layout.fillWidth: true }
                ListView {
                    id: queuedList; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10; model: []
                    delegate: Rectangle {
                        width: queuedList.width; height: queuedRow.implicitHeight + 30; color: "white"; border.color: "#888888"
                        ColumnLayout {
                            id: queuedRow; anchors.fill: parent; anchors.margins: 15; spacing: 4
                            RowLayout {
                                Layout.fillWidth: true
                                ArkControls.Label { text: String(root.outboxDestination(modelData) || "Agent").replace(/^./, function(c){ return c.toUpperCase(); }); type: ArkControls.Label.LargeStrong; Layout.fillWidth: true }
                                ArkControls.Label { text: root.outboxStatus(modelData); type: ArkControls.Label.SmallSubtle }
                            }
                            ArkControls.Caption { text: Model.submissionSource((modelData.payload||{}).source); wrapMode: Text.Wrap; Layout.fillWidth: true }
                            ArkControls.Caption { text: Model.submissionTimes(modelData, true); wrapMode: Text.Wrap; Layout.fillWidth: true }
                            ArkControls.Body { visible: !!(modelData.payload && modelData.payload.content && modelData.payload.content.mode !== "raw_image" && root.outboxText(modelData)); text: root.outboxText(modelData); elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                        MouseArea { anchors.fill: parent; onClicked: { root.selectedQueued=true;root.selectedSubmissionId=modelData.id;root.selectedSubmission=root.outboxItem(modelData.id);root.pane="detail"; } }
                    }
                }
            }
        }

        Item {
            visible: pane === "history"; Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; spacing: 10
                ArkControls.Title { text: "Submission history"; type: ArkControls.Title.Medium; Layout.fillWidth: true }
                ArkControls.Body { visible: historyList.count === 0; text: "No accepted submissions yet" }
                ListView {
                    id: historyList; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10; model: []
                    delegate: Rectangle {
                        width: historyList.width; height: historyRow.implicitHeight + 30; color: "white"; border.color: "#888888"
                        ColumnLayout {
                            id: historyRow; anchors.fill: parent; anchors.margins: 15; spacing: 4
                            RowLayout {
                                Layout.fillWidth: true
                                ArkControls.Label { text: String(modelData.destination || "Agent").replace(/^./, function(c){ return c.toUpperCase(); }); type: ArkControls.Label.LargeStrong; Layout.fillWidth: true }
                                ArkControls.Label { text: Model.submissionStatus(modelData); type: ArkControls.Label.SmallSubtle }
                            }
                            ArkControls.Caption { text: Model.submissionSource(modelData.source); wrapMode: Text.Wrap; Layout.fillWidth: true }
                            ArkControls.Caption { text: Model.submissionTimes(modelData, false); wrapMode: Text.Wrap; Layout.fillWidth: true }
                            ArkControls.Body { visible: modelData.content.mode !== "raw_image" && root.historyText(modelData).length > 0; text: root.historyText(modelData); elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                        MouseArea { anchors.fill: parent; onClicked: {
                            root.selectedQueued=false;root.selectedSubmissionId=modelData.id;root.selectedSubmission=modelData;
                            root.sourcePreview="";root.previewRequestId="";root.pane="detail";
                            if(modelData.source&&!root.historyImage(modelData))root.requestPreview(modelData.request_id);
                        } }
                    }
                }
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
                        ArkControls.Title { text: capture && capture.source.selection === "page" ? "Confirm captured page" : "Confirm original selection"; type: ArkControls.Title.Medium }
                        Image { visible: !!capture || sourcePreview.length > 0; source: capture && capture.image ? Model.imageUrl(capture.image) : sourcePreview; Layout.fillWidth: true; Layout.preferredHeight: 240; fillMode: Image.PreserveAspectFit }
                        ArkControls.Body { text: capture ? Model.sourceLabel(capture.source) : ""; type: ArkControls.Body.Small; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        ArkControls.Dropdown { id: destination; model: ["Hermes", "Muse", "Codex", "Obsidian"]; currentIndex: 1; onActivated: draft() }
                        ArkControls.Label { visible: root.convertedAvailable; text: "Converted text from this capture"; type: ArkControls.Label.LargeStrong }
                        TextArea { id: converted; visible: root.convertedAvailable; placeholderText: "Review converted text"; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredHeight: 150; onTextChanged: if (loaded) draft() }
                        ArkControls.Body { visible: !root.convertedAvailable; text: "Text conversion is unavailable for this selection. The original image can still be sent."; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        TextArea {
                            id: comment; text: root.messageText; placeholderText: "Optional typed comment"; wrapMode: Text.Wrap
                            Layout.fillWidth: true; Layout.preferredHeight: 150
                            onTextChanged: { if (root.messageText !== text) root.messageText = text; if (loaded && !restoring) draft(); }
                        }
                        ArkControls.Label { text: "Or draw a comment below"; type: ArkControls.Label.Medium }
                        ArkControls.Body { visible: unrestoredInk.length > 0; text: "The saved drawing could not be displayed, but it is still retained in this draft. Clear it explicitly before sending."; type: ArkControls.Body.Small; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Item {
                            id: inkFrame; Layout.fillWidth: true; Layout.preferredHeight: 220
                            Rectangle { anchors.fill: parent; color: "white"; border.color: "#888888" }
                            Epaper.ScreenModeItem { anchors.fill: parent; mode: Epaper.ScreenModeItem.Pen; visible: inkFrame.visible && root.panelVisible }
                            GoodBot.InkPad { id: ink; anchors.fill: parent; enabled: unrestoredInk.length === 0; onChanged: if (root.loaded && !root.restoring) root.draft(); onErrorChanged: if (error) root.notice = error }
                        }
                        ArkControls.Button { visible: unrestoredInk.length > 0 || ink.hasInk; text: "Clear drawing"; type: ArkTokens.Button.tertiary; onClicked: { unrestoredInk = ""; ink.clear(); draft(); } }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ArkControls.Button { text: "Send original image"; type: ArkTokens.Button.primary; enabled: loaded && !saving; onClicked: submit(false) }
                    ArkControls.Button { text: "Send converted text"; type: ArkTokens.Button.secondary; visible: root.convertedAvailable; enabled: loaded && !saving && converted.text.trim().length > 0; onClicked: submit(true) }
                }
            }
        }

        Item {
            visible: pane === "detail" && !!selectedSubmission; Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout { anchors.fill: parent; spacing: 12
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    ColumnLayout { width: parent.width; spacing: 12
                        ArkControls.Title { text: selectedSubmission ? (selectedQueued ? root.outboxStatus(selectedSubmission) + " · " + root.outboxDestination(selectedSubmission) : Model.submissionStatus(selectedSubmission) + " · " + (selectedSubmission.destination || "Agent")) : ""; type: ArkControls.Title.Medium; Layout.fillWidth: true }
                        ArkControls.Caption { text: selectedSubmission ? Model.submissionSource(selectedQueued ? (selectedSubmission.payload||{}).source : selectedSubmission.source) : ""; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        ArkControls.Caption { text: Model.submissionTimes(selectedSubmission, selectedQueued); wrapMode: Text.Wrap; Layout.fillWidth: true }
                        ArkControls.Caption { text: selectedSubmission ? "Stable ID: " + selectedSubmission.id : ""; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        ArkControls.Body { visible: selectedQueued && selectedSubmission && !!selectedSubmission.reason; text: "Last error: " + (selectedSubmission ? selectedSubmission.reason : ""); type: ArkControls.Body.Small; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        ArkControls.Body { visible: !selectedQueued && selectedSubmission && !!selectedSubmission.summary; text: selectedSubmission ? selectedSubmission.summary : ""; type: ArkControls.Body.Small; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        TextArea { visible: selectedSubmission && (selectedQueued ? root.outboxText(selectedSubmission) : root.historyText(selectedSubmission)).length > 0; text: selectedSubmission ? (selectedQueued ? root.outboxText(selectedSubmission) : root.historyText(selectedSubmission)) : ""; readOnly: true; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredHeight: 180 }
                        Image {
                            visible: selectedSubmission && (selectedQueued ? root.outboxImage(selectedSubmission) : root.historyImage(selectedSubmission)).length > 0
                            source: selectedSubmission ? (selectedQueued ? root.outboxImage(selectedSubmission) : root.historyImage(selectedSubmission)) : ""
                            Layout.fillWidth: true; Layout.preferredHeight: 260; fillMode: Image.PreserveAspectFit
                            MouseArea { anchors.fill: parent; enabled: selectedSubmission && !!(selectedQueued ? (selectedSubmission.payload||{}).source : selectedSubmission.source); onClicked: root.openSource(selectedQueued ? selectedSubmission.payload.source : selectedSubmission.source) }
                        }
                        ArkControls.Label { visible: selectedSubmission && (selectedQueued ? root.outboxCommentText(selectedSubmission) : root.historyComment(selectedSubmission)).length > 0; text: "Comment"; type: ArkControls.Label.LargeStrong }
                        TextArea { visible: selectedSubmission && (selectedQueued ? root.outboxCommentText(selectedSubmission) : root.historyComment(selectedSubmission)).length > 0; text: selectedSubmission ? (selectedQueued ? root.outboxCommentText(selectedSubmission) : root.historyComment(selectedSubmission)) : ""; readOnly: true; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredHeight: 140 }
                        Image { visible: selectedQueued && selectedSubmission && root.outboxCommentImage(selectedSubmission).length > 0; source: selectedSubmission ? root.outboxCommentImage(selectedSubmission) : ""; Layout.fillWidth: true; Layout.preferredHeight: 220; fillMode: Image.PreserveAspectFit }
                        ArkControls.Button { visible: selectedSubmission && !!(selectedQueued ? (selectedSubmission.payload||{}).source : selectedSubmission.source); text: "Open exact source…"; type: ArkTokens.Button.secondary; onClicked: root.openSource(selectedQueued ? selectedSubmission.payload.source : selectedSubmission.source) }
                    }
                }
                RowLayout { Layout.fillWidth: true
                    ArkControls.Button { text: selectedQueued ? "Back to Queued" : "Back to History"; type: ArkTokens.Button.tertiary; onClicked: { pane=selectedQueued?"queued":"history";selectedSubmissionId="";selectedSubmission=null;sourcePreview="";previewRequestId=""; } }
                    ArkControls.Button { text: "Retry queued item"; type: ArkTokens.Button.secondary; visible: selectedQueued && selectedSubmission && selectedSubmission.status !== "needs_review"; enabled: selectedQueued && selectedSubmission && Model.nextItem(root.state) && Model.nextItem(root.state).id === selectedSubmission.id && !saving && !dirty && !syncing; onClicked: retryOutbox(selectedSubmission.id) }
                }
            }
        }
    }
    Dialogs.ConfirmChangeDialog {
        id: openConfirm
        visible: false
        anchors.fill: parent
        title: "Open the current source page?"
        description: Model.sourceLabel(root.confirmSource) + "\nThe page may have changed since this capture."
        confirmButtonText: "Open"
        rejectButtonText: "Cancel"
        onAccepted: { visible = false; root.sourceOpenRequested(root.confirmSource); }
        onRejected: visible = false
    }
    Dialogs.ConfirmChangeDialog {
        id: unsavedClose
        visible: false
        anchors.fill: parent
        title: "Close with an unsaved draft?"
        description: "Saving failed. Closing keeps the draft in memory while the notebook app stays open. Restarting may lose these changes."
        confirmButtonText: "Close"
        rejectButtonText: "Keep open"
        onAccepted: { visible = false; root.close(); }
        onRejected: visible = false
    }
}
