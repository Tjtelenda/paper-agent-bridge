import QtQuick
import com.remarkable

// A separate client viewport renders paper and visible layers, independent of editor zoom.
Item {
    id: root
    required property var document
    required property string pageId
    required property var layout
    required property var renderProfile
    property bool configured: false
    property bool grabbing: false
    property bool finished: false
    property int settled: 0
    signal completed(var image)
    signal failed(string reason)
    x: -width - 100
    width: layout.width
    height: layout.height

    function start() { deadline.start(); poll.start(); }
    function fail(reason) { if(finished)return;finished=true;poll.stop();deadline.stop();failed(reason); }
    Component.onDestruction: { if(!finished)failed("Page capture was cancelled; no message was sent"); }
    SceneView {
        id: scene
        anchors.fill: parent
        document: root.document
        pageId: root.pageId
        viewportType: SceneView.ClientViewport
        ignoreDocumentZoomMode: true
        limitScrollingToPaper: false
        renderingTilesBeyondViewport: false
        tileSize: Qt.size(512,512)
        colorProfile: root.renderProfile
    }
    Timer {
        id: deadline
        interval: 30000
        onTriggered: root.fail("Full-page rendering timed out; your draft and page are unchanged")
    }
    Timer {
        id: poll
        interval: 50
        repeat: true
        onTriggered: {
            if(scene.pageStatus!==Page.None && scene.pageStatus!==Page.Ready) {
                root.fail("The page could not be loaded for export");return;
            }
            if(scene.status!==SceneView.Active || !scene.controller || !scene.tileManager)return;
            if(!root.configured) {
                root.configured=true;
                const b=root.layout.bounds;
                scene.setFocalPoint(Qt.point(b.x+b.width/2,b.y+b.height/2),root.layout.scale);
                scene.tileManager.requestMissingTiles();
                return;
            }
            if(scene.awaitingInitialTileSet || scene.tileManager.pendingTiles || scene.tileManager.transactionRunning) {
                root.settled=0;return;
            }
            // Let the completed native tile set reach the Qt Quick scene graph.
            if(++root.settled<2 || root.grabbing)return;
            root.grabbing=true;poll.stop();
            if(!scene.grabToImage(function(result) {
                if(root.finished)return;
                root.finished=true;deadline.stop();root.completed(result.image);
            },Qt.size(root.width,root.height)))root.fail("The full-page image could not be captured");
        }
    }
}
