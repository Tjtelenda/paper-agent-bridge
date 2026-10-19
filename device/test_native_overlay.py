import tempfile
import unittest
from pathlib import Path

from build_native_overlay import build, patch_resource, replace_once


class NativeOverlayTest(unittest.TestCase):
    def test_firmware_mismatch_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "xochitl").write_bytes(b"other firmware")
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "Unsupported xochitl"):
                build(root, output)
            self.assertFalse(output.exists())

    def test_missing_or_ambiguous_anchors_fail(self):
        for text in ("absent", "anchor anchor"):
            with self.assertRaises(ValueError):
                replace_once(text, "anchor", "new")

    def test_selection_adds_agent_and_vault_without_note_mutation(self):
        original = '''import QtQuick
Item {
    id: selectionRoot
    SelectionContextualMenu {
        ArkControls.ContextualMenu.Button {
            id: selectionDelete
        }
    }
}
'''
        patched = patch_resource("/qml/common/SceneSelectionHandler.qml", original)
        self.assertIn('objectName: "goodbotSendSelection"', patched)
        self.assertIn('objectName: "goodbotSendSelectionToVault"', patched)
        self.assertIn('iconSource: "qrc:/ark/icons/archive"', patched)
        self.assertIn('"obsidian")', patched)
        self.assertNotIn("Accessible.", patched)
        self.assertIn("selectionPreview().image.image()", patched)
        self.assertIn("controller.primaryContentRect", patched)
        self.assertIn("id: selectionDelete", patched)
        self.assertLess(patched.index('objectName: "goodbotSendSelection"'),
                        patched.index('objectName: "goodbotSendSelectionToVault"'))
        self.assertLess(patched.index('objectName: "goodbotSendSelectionToVault"'),
                        patched.index("id: selectionDelete"))
        self.assertNotIn("deleteSelectedItems", patched)
        self.assertNotIn("clearSelectedItems", patched)
        self.assertNotIn("convertSelection", patched)

        scene = '''import QtQuick
Item {
    id: root
    SceneSelectionHandler {
            id: selectionHandler
    }
}
'''
        routed = patch_resource("/qml/device/view/documentview/DeviceSceneView.qml", scene)
        self.assertIn("(image, rect, bounds, destination)", routed)
        self.assertIn("rect, bounds, destination", routed)

    def test_share_actions_reuse_stock_foldout_rows(self):
        original = '''import QtQuick
Item {
    hasExtraContent: root.nestedLayout && root.hasIntegrations
    component ShareBy: ColumnLayout {
        spacing: 0
        SendByEmailButton {
        }
    }
}
'''
        patched = patch_resource("/qt/qml/xofm/libs/toolbar/qml/ShareMenu.qml", original)
        self.assertIn('label: "Send to agent"', patched)
        self.assertIn('label: "Send to vault"', patched)
        self.assertIn('type: ToolbarTool.Type.FoldoutButton', patched)
        self.assertIn('GoodBot.Session.openSend(root.toolbar.goodbotPageProvider)', patched)
        self.assertIn('GoodBot.Session.openVault(root.toolbar.goodbotPageProvider)', patched)
        self.assertNotIn('label: "Agent inbox"', patched)
        self.assertNotIn('GoodBot.Session.openInbox()', patched)
        self.assertIn('hasExtraContent: root.nestedLayout && root.hasIntegrations', patched)
        self.assertLess(patched.index('objectName: "goodbotSendToAgent"'),
                        patched.index('objectName: "goodbotSendToVault"'))
        self.assertLess(patched.index('objectName: "goodbotSendToVault"'),
                        patched.index("SendByEmailButton {"))

    def test_settings_menu_places_inbox_after_share(self):
        original = '''import QtQuick
ToolbarTool {
    Component {
        id: settingsComponent
        ColumnLayout {
            Repeater { delegate: ToolLoader {} }
            Repeater { delegate: ToolLoader {} }
            Repeater { delegate: ToolLoader {} }
            Repeater { delegate: ToolLoader {} }
            Repeater { delegate: ToolLoader {} }
        }
    }
}
'''
        patched = patch_resource("/qt/qml/xofm/libs/toolbar/qml/SettingsMenu.qml", original)
        self.assertEqual(patched.count("delegate: SettingsToolRow {"), 5)
        self.assertNotIn("delegate: ToolLoader {", patched)
        self.assertIn('visible: settingsToolLoader.visible && settingsToolLoader.item?.objectName === "shareMenu"', patched)
        self.assertIn('label: "Agent inbox"', patched)
        self.assertIn("GoodBot.Session.openInbox()", patched)
        self.assertLess(patched.index("ToolLoader {"), patched.index('objectName: "goodbotAgentInbox"'))
        for forwarding in (
            "required property var model",
            "required property int buttonType",
            "required property Item toolbar",
            "property alias active: settingsToolLoader.active",
            "property alias asynchronous: settingsToolLoader.asynchronous",
            "property alias foldoutPosition: settingsToolLoader.foldoutPosition",
            "readonly property alias shown: settingsToolLoader.shown",
            "readonly property alias item: settingsToolLoader.item",
            "antialiasing: row.antialiasing",
            "signal select(var tool)",
            "Layout.fillHeight: false",
        ):
            self.assertIn(forwarding, patched)


if __name__ == "__main__":
    unittest.main()
