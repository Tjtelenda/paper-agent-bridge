#!/usr/bin/env python3
"""Apply guarded 3.29 UI edits to a private firmware extraction, never the repo."""
import argparse
import hashlib
import json
from pathlib import Path

FIRMWARE_SHA256 = "0d4bfba6daf8f61138996af95b494d70da11654d4861a98bffe894a425b8caba"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Firmware UI anchor is missing or ambiguous")
    return text.replace(old, new, 1)


def patch_resource(name, text):
    text = replace_once(text, "import QtQuick\n", "import QtQuick\nimport GoodBot.Bridge 1.0 as GoodBot\n")
    if name.endswith("/Sidebar.qml"):
        marker = "        ArkControls.SidebarItem {\n            id: filterBy"
        text = replace_once(text, marker, '''        ArkControls.SidebarItem {
            objectName: "goodbotAgent"
            text: "Agent"
            iconSource: "qrc:/ark/icons/send"
            Layout.preferredWidth: parent.width
            onClicked: {
                root.hide();
                GoodBot.Session.openInbox();
            }
        }

''' + marker)
    elif name.endswith("/MainView.qml"):
        text = replace_once(text, "    function openDocument_helper(entry, openCall) {", '''    function goodbotOpenSource(source) {
        const client = GoodBot.Session.panel.client;
        try {
            if (!source || !/^[A-Za-z0-9._:-]{1,160}$/.test(source.notebook_id)
                    || !/^[A-Za-z0-9._:-]{1,160}$/.test(source.page_id))
                throw Error("Source identity is unavailable");
            const entry = Library.entryForId(source.notebook_id);
            GoodBot.Session.sourcePageIndex(source, entry);
            if (entry.archived) throw Error("Retrieve this archived notebook in My files first");
            root.openDocument_helper(entry, function() {
                try {
                    const live = Library.entryForId(source.notebook_id);
                    const index = GoodBot.Session.sourcePageIndex(source, live);
                    documentView.item.openDocumentOnPage(live, index, null);
                    client.notice = "Source page opened beneath this response panel.";
                } catch (error) { client.notice = String(error); }
            });
        } catch (error) { client.notice = String(error); }
    }

    function openDocument_helper(entry, openCall) {''')
        marker = "            PopupOverlay {\n                id: _popupOverlay"
        text = replace_once(text, marker, marker + '''
                penInputManager: root.penInput.surfaceManager
                GoodBot.Panel {
                    overlay: _popupOverlay
                    enabled: !root.passcodeHandler.userLocked
                    visible: active && !root.passcodeHandler.userLocked
                    sourceOpener: function(source) { root.goodbotOpenSource(source); }
                }
''')
    elif name.endswith("/SceneSelectionHandler.qml"):
        text = replace_once(text, "    id: selectionRoot\n", "    id: selectionRoot\n    signal goodbotSelectionRequested(var image, rect sceneRect, rect pageRect)\n")
        marker = "        ArkControls.ContextualMenu.Button {\n            id: selectionDelete"
        text = replace_once(text, marker, '''        ArkControls.ContextualMenu.Button {
            objectName: "goodbotSendSelection"
            focusPolicy: Qt.NoFocus
            type: tools.type
            iconSource: "qrc:/ark/icons/send"
            enabled: !selectionRoot.working && !!selectionRoot.controller
            onClicked: selectionRoot.goodbotSelectionRequested(
                selectionRoot.selectionPreview().image.image(),
                selectionRoot.sceneSelectionRect,
                selectionRoot.controller.primaryContentRect)
        }

''' + marker)
    elif name.endswith("/DeviceSceneView.qml"):
        marker = "            id: selectionHandler\n"
        text = replace_once(text, marker, marker + '''            onGoodbotSelectionRequested: (image, rect, bounds) => {
                if (!root.document) return;
                GoodBot.Session.capture(image, root.document.id.toString(), root.pageId,
                    root.document.visibleName, String(root.page + 1), rect, bounds);
            }
''')
    elif name.endswith("/DocumentView.qml"):
        text = replace_once(text, "    property bool subDialogOpen: pages.visible\n                                 || formatSettings.visible",
                            "    property bool subDialogOpen: pages.visible\n                                 || formatSettings.visible\n                                 || root.goodbotPanelActive")
        text = replace_once(text, "readonly property bool shortcutsEnabled: visible && !adjustViewPopup.active",
                            "readonly property bool shortcutsEnabled: visible && !adjustViewPopup.active && !root.goodbotPanelActive")
        text = replace_once(text, "        gesturesEnabled: !fullscreenSubDialogOpen",
                            "        gesturesEnabled: !fullscreenSubDialogOpen && !root.goodbotPanelActive")
        text = replace_once(text, "        clickGestureEnabled: !isLoading && !_searchResultsHeader.visible",
                            "        clickGestureEnabled: !isLoading && !_searchResultsHeader.visible && !root.goodbotPanelActive")
        text = replace_once(text, "        focus: !root.subdialogOpen\n",
                            "        focus: !root.subdialogOpen\n            && !root.goodbotPanelActive\n")
        marker = "    property SceneController sceneController: sceneView.controller\n"
        text = replace_once(text, marker, marker + '''
    readonly property bool goodbotPanelActive: !!GoodBot.Session.panel && GoodBot.Session.panel.active
    ArkControls.IconButton {
        objectName: "goodbotNotebookInbox"
        iconSource: "qrc:/qml/images/inbox.svg"
        type: ArkTokens.IconButton.primary
        buttonSize: ArkControls.IconButton.Size.Large
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        z: 100
        visible: root.documentLoaded
        onClicked: GoodBot.Session.openInbox()
    }
''')
    else:
        raise ValueError("Unsupported firmware resource")
    return text


RESOURCES = (
    "qml/device/view/navigator/Sidebar.qml",
    "qml/device/view/main/MainView.qml",
    "qml/common/SceneSelectionHandler.qml",
    "qml/device/view/documentview/DeviceSceneView.qml",
    "qml/device/view/documentview/DocumentView.qml",
)


def build(firmware, output):
    if hashlib.sha256((firmware / "xochitl").read_bytes()).hexdigest() != FIRMWARE_SHA256:
        raise ValueError("Unsupported xochitl build; inspect the new firmware before patching")
    # Validate all inputs before creating output. Never overwrite an earlier build.
    patched = {name: patch_resource(name, (firmware / "resources" / name).read_text()) for name in RESOURCES}
    output.mkdir(mode=0o700)
    lines = []
    for index, (name, text) in enumerate(patched.items()):
        target = f"goodbot-{index}.qml"
        (output / target).write_text(text)
        lines.append(f"R /{name} {target}")
    (output / "goodbot.qrr").write_text("\n".join(lines) + "\n")
    (output / "build.json").write_text(json.dumps({"firmware_sha256": FIRMWARE_SHA256, "resources": list(patched)}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.firmware, args.output)
