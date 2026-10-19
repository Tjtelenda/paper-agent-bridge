// Native controllers are supplied by the owning QML component; never evaluate commands.
function canonical(v) { if(Array.isArray(v))return "["+v.map(canonical).join(",")+"]";if(v&&typeof v==='object')return "{"+Object.keys(v).sort().map(function(k){return JSON.stringify(k)+":"+canonical(v[k]);}).join(",")+"}";return JSON.stringify(v); }
function requireValue(ok, message) { if (!ok) throw Error(message); }
function keys(args, required, optional) {
    requireValue(args && typeof args === 'object' && !Array.isArray(args), 'Arguments must be an object');
    Object.keys(args).forEach(function(k) { requireValue(required.indexOf(k)>=0 || optional.indexOf(k)>=0, 'Unexpected argument: '+k); });
    required.forEach(function(k) { requireValue(Object.prototype.hasOwnProperty.call(args,k), 'Missing argument: '+k); });
}
function identifier(value) { requireValue(typeof value==='string' && /^[A-Za-z0-9._:-]{1,160}$/.test(value), 'Invalid native identity');return value; }
function name(value) { requireValue(typeof value==='string' && value.trim()===value && value.length>0 && value.length<=200 && !/[\x00-\x1f/\\]/.test(value) && value!=='.' && value!=='..', 'Invalid library name');return value; }
function ids(doc) { var pages=[];for(var i=0;i<doc.pageCount;i++)pages.push(String(doc.idForPage(i)));return pages; }
function entry(c,id) { var e=c.library.entryForId(id===''?'':identifier(id));requireValue(e && String(e.id)===id, 'Native item not found');return e; }
function document(c,id) {var e=entry(c,id);requireValue(e.type===c.documentType && !e.isTrashed && !e.archived && !e.isExporting && !e.isDownloading,'Notebook unavailable or busy');return e;}
function snapshot(c,e) {
    var result={id:String(e.id),name:String(e.visibleName),parent_id:String(e.parentId),trashed:!!e.isTrashed,type:e.type};
    if(e.type===c.documentType){result.pages=ids(e);result.templates=[];for(var i=0;i<e.pageCount;i++)result.templates.push(String(e.templateForPage(i)));result.imported=!!e.isImported;if(c.view&&c.view.document&&String(c.view.document.id)===String(e.id)){result.active_page_id=String(c.view.currentPageId);result.text_length=c.view.sceneController.hasRootDocument?c.view.sceneController.rootDocumentLength:0;}}
    else if(e.childIds!==undefined)result.children=Array.prototype.map.call(e.childIds,function(id){return String(id);});
    return result;
}
function expected(c,a) {
    identifier(a.id);var e=entry(c,a.id),actual=snapshot(c,e);
    requireValue(a.expected && canonical(JSON.parse(JSON.stringify(actual)))===canonical(JSON.parse(JSON.stringify(a.expected))), 'Item changed; inspect again');
    return e;
}
function templateName(c,value) { requireValue(typeof value==='string' && c.templates().some(function(t){return t.filename===value;}), 'Template is not installed');return value; }
function folder(c,id) {var e=entry(c,id);requireValue(e.childIds!==undefined && !e.isTrashed,'Destination is not an available folder');return e;}
function unique(c,parent,label,except) {
    var f=folder(c,parent);Array.prototype.forEach.call(f.childIds,function(id){var e=entry(c,String(id));requireValue(String(id)===except || String(e.visibleName).toLowerCase()!==label.toLowerCase(),'Name collision in destination');});
}
function pages(doc,values) {
    requireValue(Array.isArray(values)&&values.length>0&&values.length<=200,'Select 1–200 exact page IDs');
    var seen={},result=values.map(function(id){identifier(id);requireValue(!seen[id],'Duplicate page ID');seen[id]=true;var index=doc.pageForId(id);requireValue(index>=0&&index<doc.pageCount&&String(doc.idForPage(index))===id,'Page no longer exists');return index;});
    return result;
}
function backup(a) {requireValue(a.confirmed===true && typeof a.backup_sha256==='string' && /^[0-9a-f]{64}$/.test(a.backup_sha256),'Destructive operation needs explicit confirmation and a verified backup');}
function prepare(c,op,a) {
    // QML QVariantList wrappers are not JavaScript arrays.
    a=JSON.parse(JSON.stringify(a));
    var e,before,index,target,check,view,scene;
    if(op==='inspect') {keys(a,[],['id']);var id=a.id||c.rootId();var inspected=snapshot(c,entry(c,id));if(id===''&&c.view&&c.view.document)inspected.active_editor={id:String(c.view.document.id),page_id:String(c.view.currentPageId)};return {immediate:inspected};}
    if(op==='list_templates') {keys(a,[],[]);return {immediate:{templates:c.templates().map(function(t){return {filename:String(t.filename),name:String(t.name),landscape:!!t.landscape};})}};}
    if(op==='list_folder') {keys(a,[],['id']);e=folder(c,a.id||c.rootId());return {immediate:{folder:snapshot(c,e),items:Array.prototype.map.call(e.childIds,function(id){return snapshot(c,entry(c,String(id)));})}};}
    if(op==='create_folder'||op==='create_notebook') {
        keys(a,['parent_id','name'],op==='create_notebook'?['template','landscape','document_id','page_id']:[]);
        requireValue(a.parent_id===''||identifier(a.parent_id),'Invalid parent');name(a.name);unique(c,a.parent_id,a.name,'');
        var id;
        if(op==='create_notebook') {
            templateName(c,a.template||'Blank');
            if(a.document_id){identifier(a.document_id);identifier(a.page_id);id=String(c.lc.createDocument(a.parent_id,a.name,a.document_id,a.page_id));}
            else id=String(c.lc.createDocument(a.parent_id,a.name));
            e=document(c,id);c.lc.setOrientation(id,a.landscape?c.horizontal:c.vertical);
            c.dc.setTemplateForPage(id,0,a.template||'Blank',c.paperSize);c.lc.setCoverPageNumber(id,0);
        } else id=String(c.lc.createCollection(a.parent_id,a.name));
        identifier(id);return {poll:function(){var item=entry(c,id);return String(item.visibleName)===a.name&&String(item.parentId)===a.parent_id?snapshot(c,item):null;}};
    }
    if(op==='copy_item') {
        keys(a,['id','expected','parent_id'],[]);e=expected(c,a);document(c,a.id);target=folder(c,a.parent_id);
        before=Array.prototype.map.call(target.childIds,function(id){return String(id);});var sourceBefore=ids(e);
        c.lc.cloneEntry(e.id,target.id,false);
        return {poll:function(){var added=Array.prototype.map.call(target.childIds,function(id){return String(id);}).filter(function(id){return before.indexOf(id)<0;});if(added.length===0)return null;requireValue(added.length===1,'Ambiguous copied item');var copied=document(c,added[0]);requireValue(canonical(ids(e))===canonical(sourceBefore),'Source changed during copy');return copied.pageCount===sourceBefore.length?snapshot(c,copied):null;}};
    }
    if(op==='create_from_pages') {
        keys(a,['id','expected','page_ids','parent_id','name'],['confirmed','backup_sha256']);e=expected(c,a);document(c,a.id);backup(a);
        index=pages(e,a.page_ids);before=ids(e);requireValue(index.length<e.pageCount,'Leave at least one source page');target=folder(c,a.parent_id);name(a.name);unique(c,a.parent_id,a.name,'');
        var createdId=String(c.lc.createDocumentFromExisting(target.id,a.name,e.id,index));identifier(createdId);
        var wantedSource=before.filter(function(id){return a.page_ids.indexOf(id)<0;});
        return {poll:function(){var made=document(c,createdId);return canonical(ids(e))===canonical(wantedSource)&&made.pageCount===index.length?{source:snapshot(c,e),target:snapshot(c,made),moved_source_page_ids:a.page_ids}:null;}};
    }
    if(op==='quick_sheet') {
        keys(a,[],[]);e=c.lc.quickSheets();requireValue(e && e.type===c.documentType && !e.isExporting && !e.isDownloading,'Quick sheets unavailable');
        before=ids(e);if(e.hasContentsOnPage(e.pageCount-1))c.dc.addPageWithTemplateAndPageSize(e.id,-1,'Blank',c.paperSize);
        else return {immediate:snapshot(c,e)};
        return {poll:function(){return e.pageCount===before.length+1?snapshot(c,e):null;}};
    }
    if(op==='rename'||op==='move_item') {
        keys(a,['id','expected',op==='rename'?'name':'parent_id'],[]);e=expected(c,a);target=op==='rename'?String(e.parentId):(a.parent_id===''?'':identifier(a.parent_id));
        var label=op==='rename'?name(a.name):String(e.visibleName);unique(c,target,label,a.id);
        if(op==='move_item') {var ancestor=target,seen={};while(ancestor){requireValue(ancestor!==a.id&&!seen[ancestor],'Cannot move a folder into itself');seen[ancestor]=true;var p=c.library.parentIdForId(ancestor);if(!p||String(p)===ancestor)break;ancestor=String(p);}requireValue(c.lc.moveEntries([e.id],folder(c,target).id)!==false,'Native move rejected');}
        else requireValue(c.lc.setVisibleName(a.id,label)!==false,'Native rename rejected');
        return {poll:function(){var n=entry(c,a.id);return String(n.visibleName)===label&&String(n.parentId)===target?snapshot(c,n):null;}};
    }
    if(op==='trash'||op==='restore'||op==='delete_item') {
        keys(a,['id','expected'],op==='delete_item'?['confirmed','backup_sha256']:[]);e=expected(c,a);
        if(op==='delete_item'){backup(a);requireValue(!c.view||!c.view.document||String(c.view.document.id)!==a.id,'Close the target before permanent deletion');requireValue(e.isTrashed,'Permanent deletion requires an item already in Trash');requireValue(c.lc.deleteEntries([e.id])!==false,'Delete rejected');return {poll:function(){return c.library.entryForId(a.id)?null:{id:a.id,deleted:true};}};}
        requireValue(op==='trash'?!e.isTrashed:!!e.isTrashed,'Unexpected Trash state');
        requireValue((op==='trash'?c.lc.moveEntriesToTrash([e.id]):c.lc.restoreEntriesFromTrash([e.id]))!==false,'Trash action rejected');
        return {poll:function(){var n=entry(c,a.id);return !!n.isTrashed===(op==='trash')?snapshot(c,n):null;}};
    }
    if(op==='append_page'||op==='set_template') {
        keys(a,['id','expected','template'],op==='append_page'?['index','page_id']:['page_id']);e=expected(c,a);document(c,a.id);before=ids(e);templateName(c,a.template);
        if(op==='append_page') {index=a.index===undefined?-1:a.index;requireValue(Number.isInteger(index)&&index>=-1&&index<=e.pageCount,'Invalid insertion index');
            if(a.page_id)c.dc.addPageWithTemplateAndPageSize(a.id,index,a.template,c.paperSize,function(){},identifier(a.page_id));
            else c.dc.addPageWithTemplateAndPageSize(a.id,index,a.template,c.paperSize);
            return {poll:function(){var list=ids(e);if(list.length!==before.length+1)return null;var added=list.filter(function(p){return before.indexOf(p)<0;});requireValue(added.length===1&&(!a.page_id||added[0]===a.page_id),'Unexpected added page');var at=list.indexOf(added[0]);requireValue(at===(index===-1?before.length:index),'Unexpected page position');return snapshot(c,e);}};
        }
        index=pages(e,[a.page_id])[0];requireValue(e.isNotepage(index),'Template changes require a native note page');c.dc.setTemplateForPage(a.id,index,a.template);
        return {poll:function(){requireValue(JSON.stringify(ids(e))===JSON.stringify(before),'Page order changed');return String(e.templateForPage(index))===a.template?snapshot(c,e):null;}};
    }
    if(op==='copy_pages'||op==='move_pages'||op==='delete_pages') {
        keys(a,['id','expected','page_ids'],op==='delete_pages'?['confirmed','backup_sha256']:['target_id','target_expected','index','confirmed','backup_sha256']);
        e=expected(c,a);document(c,a.id);index=pages(e,a.page_ids);before=ids(e);
        if(op==='delete_pages'){backup(a);requireValue(!c.view||!c.view.document||String(c.view.document.id)!==a.id,'Close the target before page deletion');requireValue(index.length<e.pageCount,'Cannot delete every page');requireValue(c.dc.deletePages(a.id,index)!==false,'Page deletion rejected');return {poll:function(){var current=ids(e);return current.length===before.length-index.length&&a.page_ids.every(function(p){return current.indexOf(p)<0;})?snapshot(c,e):null;}};}
        target=expected(c,{id:a.target_id,expected:a.target_expected});document(c,a.target_id);var targetBefore=ids(target),insert=a.index===undefined?target.pageCount:a.index;
        requireValue(Number.isInteger(insert)&&insert>=0&&insert<=target.pageCount,'Invalid target index');
        if(a.id===a.target_id&&op==='move_pages') {
            backup(a);var selected=a.page_ids.slice(),remaining=before.filter(function(p){return selected.indexOf(p)<0;});
            var adjusted=insert-index.filter(function(i){return i<insert;}).length;
            var wanted=remaining.slice();wanted.splice.apply(wanted,[adjusted,0].concat(selected));
            requireValue(c.dc.movePages(a.id,index,a.target_id,insert,function(){})!==false,'Page reorder rejected');
            return {poll:function(){return canonical(ids(e))===canonical(wanted)?snapshot(c,e):null;}};
        }
        if(op==='move_pages'){backup(a);requireValue(index.length<e.pageCount,'Cannot move every source page');requireValue(c.dc.movePages(a.id,index,a.target_id,insert,function(){})!==false,'Page move rejected');}
        else requireValue(c.dc.copyPages(a.id,index,a.target_id,insert)!==false,'Page copy rejected');
        return {poll:function(){return target.pageCount===targetBefore.length+index.length&&(op==='copy_pages'||e.pageCount===before.length-index.length)?{source:snapshot(c,e),target:snapshot(c,target)}:null;}};
    }
    if(op==='open_page') {
        keys(a,['id','page_id'],[]);e=document(c,a.id);index=pages(e,[a.page_id])[0];requireValue(c.view,'Native document view unavailable');c.view.openDocumentOnPage(e,index,null);
        return {poll:function(){return c.view.document&&String(c.view.document.id)===a.id&&String(c.view.currentPageId)===a.page_id&&!c.view.isWorking?{id:a.id,page_id:a.page_id}:null;}};
    }
    if(op==='insert_text'||op==='insert_image') {
        keys(a,['id','page_id','expected'],op==='insert_text'?['text','expected_text_length']:['image_sha256','image_format','x','y']);
        e=expected(c,a);document(c,a.id);index=pages(e,[a.page_id])[0];view=c.view;
        requireValue(view&&view.document&&String(view.document.id)===a.id&&String(view.currentPageId)===a.page_id&&!view.isWorking&&e.isNotepage(index),'Open the exact writable native page before inserting');scene=view.sceneController;
        if(op==='insert_text') {
            requireValue(typeof a.text==='string'&&a.text.length>0&&a.text.length<=16000,'Text must contain 1–16000 characters');
            before=scene.hasRootDocument?scene.rootDocumentLength:0;
            requireValue(a.expected_text_length===before,'Text changed; inspect active page again');view.enterTextMode(true);scene.setCursorIndex(scene.rootDocumentLength);scene.replaceText(a.text);c.hideKeyboard();
            return {poll:function(){return scene.rootDocumentLength>=before+a.text.length?{id:a.id,page_id:a.page_id,text_length:scene.rootDocumentLength,appended_characters:a.text.length}:null;}};
        }
        var url=c.imageUrl(a.image_sha256,a.image_format);requireValue(String(url).length>0,'Image asset validation failed');
        requireValue(typeof a.x==='number'&&isFinite(a.x)&&typeof a.y==='number'&&isFinite(a.y)&&Math.abs(a.x)<=100000&&Math.abs(a.y)<=100000,'Explicit finite image coordinates required');
        scene.insertImageFileAsSceneItem(url,c.point(a.x,a.y));
        return {poll:function(){return {id:a.id,page_id:a.page_id,image_sha256:a.image_sha256};}};
    }
    throw Error('Operation is not yet implemented: '+op);
}
