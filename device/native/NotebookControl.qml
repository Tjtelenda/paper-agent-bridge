import QtQuick
import com.remarkable
import xofm.bindings.qt
import xofm.libs.library
import xofm.libs.devicescreen
import GoodBot.Bridge 1.0 as GoodBot
import "NotebookControl.js" as Logic

QtObject {
    id: root
    property var activeView: null
    property var pending: null
    property QtObject endpoint: GoodBot.ControlEndpoint {
        acceptsCommands: !!GoodBot.Session.panel && GoodBot.Session.panel.enabled && !GoodBot.Session.capturingPage
        onRequestReceived: function(request) { root.dispatch(request); }
    }
    property Timer poll: Timer { interval:100;repeat:true;onTriggered:root.check(); }
    function context() {
        return {library:Library,lc:LibraryController,dc:DocumentController,documentType:Entry.Document,
            templates:function(){return Templates.list();},paperSize:DeviceScreenInfo.paperSize,
            vertical:Qt.Vertical,horizontal:Qt.Horizontal,view:activeView,
            rootId:function(){return "";},
            imageUrl:function(sha,format){return endpoint.imageUrl(sha,format);},point:function(x,y){return Qt.point(x,y);},
            hideKeyboard:function(){Qt.inputMethod.hide();}};
    }
    function finish(status,result) {
        if(!pending)return;
        var id=pending.id;pending=null;poll.stop();endpoint.complete(id,{status:status,result:result});
    }
    function dispatch(request) {
        pending={id:request.request_id,operation:request.operation,args:request.arguments,started:Date.now(),check:null,beforePageHash:"",beforeImages:[]};
        try {
            if(request.operation==="insert_text"||request.operation==="insert_image")pending.beforePageHash=endpoint.pageSha256(request.arguments.id,request.arguments.page_id);
            if(request.operation==="insert_image")pending.beforeImages=endpoint.pageImages(request.arguments.id,request.arguments.page_id).map(function(x){return x.filename;});
            var result=Logic.prepare(context(),request.operation,request.arguments);
            if(result.immediate){finish("verified",result.immediate);return;}
            pending.check=result.poll;poll.start();check();
        }catch(error){finish("needs_review",{reason:String(error),mutation_may_have_started:true});}
    }
    function saved(result) {
        if(pending.operation==="open_page")return true;
        if(pending.operation==="insert_text"||pending.operation==="insert_image") {
            var hash=endpoint.pageSha256(pending.args.id,pending.args.page_id);
            if(!hash||hash===pending.beforePageHash)return false;
            if(pending.operation==="insert_image") {
                var added=endpoint.pageImages(pending.args.id,pending.args.page_id).filter(function(x){return pending.beforeImages.indexOf(x.filename)<0;});
                if(added.length!==1)return false;
                result.saved_image=added[0];
            }
            result.page_data_sha256=hash;return true;
        }
        if(result.source&&result.target)return savedItem(result.source)&&savedItem(result.target);
        return savedItem(result);
    }
    function savedItem(item) {
        if(item.deleted)return endpoint.itemFilesAbsent(item.id);
        if(!item.id)return false;
        var state=JSON.parse(JSON.stringify(endpoint.savedState(item.id))),meta=state.metadata;
        if(!meta)return false;
        if(meta.visibleName!==item.name)return false;
        if((Boolean(meta.deleted)||String(meta.parent)==="trash")!==Boolean(item.trashed))return false;
        if(!item.trashed&&String(meta.parent)!==String(item.parent_id))return false;
        if(item.pages) {
            if(!state.content||state.content.pageCount!==item.pages.length)return false;
            var hasModernPages=!!state.content.cPages&&Array.isArray(state.content.cPages.pages);
            var modernPages=hasModernPages?state.content.cPages.pages.filter(function(p){return !(p.deleted&&p.deleted.value);}):[];
            if(hasModernPages) {
                modernPages.sort(function(a,b){var x=String((a.idx||{}).value||''),y=String((b.idx||{}).value||'');return x<y?-1:x>y?1:0;});
                if(JSON.stringify(modernPages.map(function(p){return p.id;}))!==JSON.stringify(item.pages))return false;
                if(item.templates&&JSON.stringify(modernPages.map(function(p){return p.template?p.template.value:"";}))!==JSON.stringify(item.templates))return false;
                if(pending.operation==='create_notebook') {
                    if(state.content.orientation!==(pending.args.landscape?'landscape':'portrait')||state.content.coverPageNumber!==0)return false;
                    if(modernPages.length!==1||modernPages[0].template.value!==(pending.args.template||'Blank'))return false;
                }
                if(pending.operation==='append_page') {
                    var added=modernPages.filter(function(p){return pending.args.expected.pages.indexOf(p.id)<0;});
                    if(added.length!==1||added[0].template.value!==pending.args.template)return false;
                }
            } else {
                if(['create_notebook','append_page','set_template'].indexOf(pending.operation)>=0)return false;
                if(!Array.isArray(state.content.pages)||JSON.stringify(state.content.pages)!==JSON.stringify(item.pages))return false;
            }
        }
        item.saved_metadata_sha256=state.metadata_sha256;
        if(state.content_sha256)item.saved_content_sha256=state.content_sha256;
        return true;
    }
    function check() {
        if(!pending)return;
        if(!endpoint.isActive(pending.id)){pending=null;poll.stop();return;}
        try {
            var result=pending.check();
            if(result && saved(result)){result.native_model_readback=true;result.saved_state_readback=true;finish("verified",result);}
            else if(Date.now()-pending.started>18000)finish("needs_review",{reason:"Native read-back timed out; reconcile before retrying",mutation_may_have_started:true});
        }catch(error){finish("needs_review",{reason:String(error),mutation_may_have_started:true});}
    }
}
