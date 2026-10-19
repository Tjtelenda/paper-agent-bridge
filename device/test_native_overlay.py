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

    def test_selection_adds_no_note_mutation(self):
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
        self.assertIn("selectionPreview().image.image()", patched)
        self.assertIn("controller.primaryContentRect", patched)
        self.assertIn("id: selectionDelete", patched)
        self.assertNotIn("deleteSelectedItems", patched)
        self.assertNotIn("clearSelectedItems", patched)
        self.assertNotIn("convertSelection", patched)

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
        self.assertIn('label: "Agent inbox"', patched)
        self.assertIn('type: ToolbarTool.Type.FoldoutButton', patched)
        self.assertIn('GoodBot.Session.openSend()', patched)
        self.assertIn('GoodBot.Session.openInbox()', patched)
        self.assertIn('hasExtraContent: root.nestedLayout && root.hasIntegrations', patched)


if __name__ == "__main__":
    unittest.main()
