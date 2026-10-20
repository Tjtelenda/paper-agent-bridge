import QtQuick

// A second native viewport crashed this firmware; retain the caller contract safely.
Item {
    required property var document
    required property string pageId
    required property var layout
    required property var renderProfile
    signal completed(var image)
    signal failed(string reason)

    function start() {
        failed("Full-page capture is unavailable. Select writing with the lasso, then send to agent or vault.");
    }
}
