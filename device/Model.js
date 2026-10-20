// Pure shared model: executable by QML and the host Node regression test.
function fresh() { return {version:1,cursor:0,outbox:[],events:[],captures:{},draft:null,last_capture:null}; }
function restore(value) {
    if (!value || Object.keys(value).length === 0) return fresh();
    if (value.version !== 1 || !Array.isArray(value.outbox) || !Array.isArray(value.events)
            || typeof value.cursor !== 'number' || !value.captures) throw Error('Unsupported saved state; preserved on disk');
    if(value.last_capture===undefined)value.last_capture=null;
    return value;
}
function uid() { return 'rm-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,12); }
function imageUrl(image) { return image ? 'data:'+image.media_type+';base64,'+image.data_base64 : ''; }
function imageFromUrl(url) {
    var match=/^data:(image\/(?:png|jpeg));base64,([A-Za-z0-9+/=\r\n]+)$/.exec(url);
    if(!match)throw Error('PNG or JPEG image required');
    return {media_type:match[1],data_base64:match[2]};
}
function selectionCapture(encoded, notebook, page, title, label, rect, bounds) {
    if(!encoded||encoded.error)throw Error(encoded&&encoded.error||'Selection image is unavailable');
    if(!/^[a-f0-9]{64}$/.test(encoded.sha256))throw Error('Selection digest is unavailable');
    if(!/^[A-Za-z0-9._:-]{1,160}$/.test(notebook)||!/^[A-Za-z0-9._:-]{1,160}$/.test(page))
        throw Error('Notebook or page identity is unavailable');
    if(!rect||!bounds)throw Error('Page geometry is unavailable');
    [rect,bounds].forEach(function(r){
        ['x','y','width','height'].forEach(function(k){if(typeof r[k]!=='number'||!isFinite(r[k]))throw Error('Page geometry is invalid');});
        if(r.width<=0||r.height<=0)throw Error('Page geometry is empty');
    });
    if(rect.x<bounds.x||rect.y<bounds.y||rect.x+rect.width>bounds.x+bounds.width||rect.y+rect.height>bounds.y+bounds.height)
        throw Error('This selection extends beyond the page; sending it is not supported yet');
    var x=(rect.x-bounds.x)/bounds.width,y=(rect.y-bounds.y)/bounds.height;
    var right=(rect.x+rect.width-bounds.x)/bounds.width,bottom=(rect.y+rect.height-bounds.y)/bounds.height;
    checkImage(encoded.image,4*1024*1024);
    return {image:encoded.image,source:{notebook_id:notebook,page_id:page,
        notebook_title:String(title).slice(0,300),page_label:String(label).slice(0,300),
        revision:'render-sha256:'+encoded.sha256,selection:'crop',
        crop:{x:x,y:y,width:right-x,height:bottom-y},captured_by:'remarkable',captured_at:new Date().toISOString()}};
}
function pageLayout(paper, content) {
    function valid(r) { return r && ['x','y','width','height'].every(function(k){return typeof r[k]==='number'&&isFinite(r[k]);})&&r.width>0&&r.height>0; }
    if(!valid(paper))throw Error('Full-page bounds are unavailable');
    if(content&&content.width===0&&content.height===0)content=paper;
    if(!valid(content))throw Error('Full-page content bounds are unavailable');
    var x=Math.min(paper.x,content.x),y=Math.min(paper.y,content.y);
    var width=Math.max(paper.x+paper.width,content.x+content.width)-x;
    var height=Math.max(paper.y+paper.height,content.y+content.height)-y;
    var scale=Math.min(1,2048/width,8192/height,Math.sqrt(16*1024*1024/(width*height)));
    if(!isFinite(scale)||scale<=0||width*scale<256||height*scale<256)
        throw Error('This page is too long or narrow for a readable image; send a selection instead');
    return {bounds:{x:x,y:y,width:width,height:height},width:Math.ceil(width*scale),height:Math.ceil(height*scale),scale:scale};
}
function pageCapture(encoded, notebook, page, title, label) {
    var bounds={x:0,y:0,width:1,height:1};
    var capture=selectionCapture(encoded,notebook,page,title,label,bounds,bounds);
    capture.source.selection='page';delete capture.source.crop;
    return capture;
}
function checkImage(image, maximum) {
    if(!image||['image/png','image/jpeg'].indexOf(image.media_type)<0||typeof image.data_base64!=='string')throw Error('PNG or JPEG required');
    var data=image.data_base64.replace(/\s/g,'');
    if(!/^[A-Za-z0-9+/]*={0,2}$/.test(data)||data.length%4!==0)throw Error('Invalid image encoding');
    var bytes=data.length/4*3-(data.endsWith('==')?2:data.endsWith('=')?1:0);
    if(!bytes||bytes>maximum)throw Error('Image is too large');
}
function nextItem(state) { for(var i=0;i<state.outbox.length;i++)if(state.outbox[i].status!=='needs_review')return state.outbox[i];return null; }
function reject(state, id, reason) {
    var item=state.outbox.filter(function(i){return i.id===id;})[0];
    if(!item)throw Error('Unknown rejected item');
    item.status='needs_review';item.reason=reason;
    state.events.push({kind:'needs_review',request_id:item.payload.request_id,status:reason});
}
function enqueue(state, item, capture, keepDraft) {
    var captures={};Object.keys(state.captures).forEach(function(k){captures[k]=state.captures[k];});
    if(capture)captures[item.id]=capture;
    var next={version:state.version,cursor:state.cursor,outbox:state.outbox.concat([item]),events:state.events,
        captures:captures,draft:keepDraft?state.draft:null,last_capture:keepDraft?(state.last_capture||null):capture};
    if(unescape(encodeURIComponent(JSON.stringify(next))).length>24*1024*1024)
        throw Error('Local storage limit reached; draft preserved. Export/archive state before adding captures');
    state.outbox=next.outbox;state.captures=captures;state.draft=next.draft;state.last_capture=next.last_capture;
    return item.id;
}
function queue(state, capture, destination, converted, comment, ink, keepDraft) {
    if (!capture || !capture.source || !capture.image) throw Error('Capture handoff required');
    if (['muse','hermes','codex','obsidian'].indexOf(destination)<0) throw Error('Choose a destination');
    checkImage(capture.image,4*1024*1024);
    if(converted!==null&&converted.length>16000)throw Error('Converted text must be at most 16,000 characters');
    if(comment.length>4000)throw Error('Comment must be at most 4,000 characters');
    if(ink)checkImage(imageFromUrl(ink),1024*1024);
    var content=converted===null?{mode:'raw_image',image:capture.image}:{mode:'converted_text',text:converted,source_image:capture.image};
    if(converted!==null && !converted.trim())throw Error('Conversion text is missing; use raw or supply verified text');
    var id=uid(),payload={request_id:id,destination:destination,source:capture.source,content:content};
    if(ink)payload.comment={mode:'ink',image:imageFromUrl(ink)};
    else if(comment.trim())payload.comment={mode:'text',text:comment};
    if(JSON.stringify(payload).length>8*1024*1024)throw Error('Capture exceeds relay limit');
    return enqueue(state,{id:id,route:3,created_at:new Date().toISOString(),payload:payload},capture,keepDraft);
}
function accept(state, receipt) {
    var item=nextItem(state);
    if(!item || (receipt.message_id||receipt.request_id)!==item.id)throw Error('Unexpected receipt; outbox preserved');
    var payload=item.payload||{},content=payload.content||{},comment=payload.comment||null;
    state.events.push({kind:'device_message',request_id:payload.request_id,message_id:payload.message_id||item.id,
        in_reply_to:payload.in_reply_to||null,destination:payload.destination||null,source:payload.source||null,
        content:content.mode==='raw_text'?{mode:'raw_text',text:content.text}:
            content.mode==='converted_text'?{mode:'converted_text',text:content.text}:{mode:'raw_image'},
        comment:comment&&comment.mode==='text'?{mode:'text',text:comment.text}:comment?{mode:'ink'}:null,
        status:receipt.status,created_at:item.created_at||''});
    state.events.push({kind:'local_receipt',request_id:receipt.request_id,status:receipt.status});
    if(item.route===3&&state.captures[item.id])state.captures[item.id]={source:state.captures[item.id].source};
    state.outbox.splice(state.outbox.indexOf(item),1);
}
function ingest(state, response) {
    if(!response || !Array.isArray(response.events) || typeof response.cursor!=='number' || response.cursor<state.cursor)throw Error('Invalid inbox response');
    var previous=state.cursor;
    response.events.forEach(function(e){
        if(typeof e.cursor!=='number'||e.cursor<=previous||e.cursor>response.cursor)throw Error('Invalid inbox ordering');
        previous=e.cursor;
    });
    response.events.forEach(function(e){state.events.push(e);});
    state.cursor=response.cursor;
}
function sourceLabel(source) {
    if(!source)return 'Original notebook';
    return (source.notebook_title||'Notebook')+' · '+(source.page_label||'Selected page');
}
function submissionSource(source) {
    if(!source)return 'Source: unavailable';
    var parts=[source.notebook_title||'Document title unavailable'];
    parts.push(source.page_label?'Page '+source.page_label:'Page unavailable');
    if(source.selection==='crop')parts.push('Lasso selection');
    else if(source.selection==='page')parts.push('Whole page');
    return 'Source: '+parts.join(' · ');
}
function submissionTime(value) {
    if(typeof value!=='string'||!/(Z|[+-]\d{2}:?\d{2})$/.test(value))return 'unavailable';
    var date=new Date(value);
    if(!isFinite(date.getTime()))return 'unavailable';
    function pad(n){return n<10?'0'+n:String(n);}
    var offset=-date.getTimezoneOffset(),zone='UTC'+(offset<0?'-':'+')+pad(Math.floor(Math.abs(offset)/60))+':'+pad(Math.abs(offset)%60);
    return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())+' '+pad(date.getHours())+':'+pad(date.getMinutes())+' ('+zone+', device time)';
}
function submissionTimes(item, queued) {
    if(!item)return '';
    var text='Submitted: '+submissionTime(queued?item.created_at:item.submitted_at);
    return queued?text:text+'\nAccepted: '+submissionTime(item.accepted_at);
}
function sourcePageIndex(source, entry) {
    if(!source||!entry||typeof entry.pageForId!=='function'||typeof entry.idForPage!=='function')
        throw Error('Source notebook is unavailable');
    var index=entry.pageForId(source.page_id);
    if(typeof index!=='number'||!isFinite(index)||Math.floor(index)!==index||index<0||index>=entry.pageCount
            ||String(entry.idForPage(index))!==source.page_id)
        throw Error('Source page is unavailable; no other page was opened');
    return index;
}
function historySubmissions(state) {
    var items=[],byId={};
    function ensure(id, event) {
        if(!id)return null;
        var key='@'+id,item=byId[key];
        if(!item) {
            item={id:id,request_id:event.request_id||id,message_id:event.message_id||id,
                destination:event.destination||'',source:event.source||null,content:{},comment:null,
                created_at:event.created_at||'',submitted_at:'',accepted_at:'',status:'accepted',stage:'',stage_state:'',summary:''};
            byId[key]=item;items.push(item);
        }
        if(event.destination)item.destination=event.destination;
        if(event.source)item.source=event.source;
        if(event.created_at&&!item.created_at)item.created_at=event.created_at;
        return item;
    }
    state.events.forEach(function(e){
        var item=null;
        if(e.kind==='device_message') {
            item=ensure(e.message_id||e.request_id,e);
            item.content=e.content||item.content;item.comment=e.comment||item.comment;
            if(e.created_at&&!item.submitted_at)item.submitted_at=e.created_at;
            item.status=e.status||item.status;
        } else if(e.kind==='capture_accepted'||e.kind==='conversation_accepted') {
            item=ensure(e.request_id,e);
            if(e.created_at&&!item.accepted_at)item.accepted_at=e.created_at;
            if(!item.content.mode)item.content=e.original||{};
        } else if(e.kind==='handoff_update') {
            item=ensure(e.message_id||e.request_id,e);item.status=e.status||item.status;
        } else if(e.kind==='local_receipt') {
            item=byId['@'+e.request_id];if(item&&e.status)item.status=e.status;
        } else if(e.kind==='stage_update') {
            item=ensure(e.request_id,e);item.stage=e.stage||'';item.stage_state=e.state||'';
            item.summary=summary(e);
        } else if(e.kind==='needs_review') {
            item=byId['@'+e.request_id];if(item){item.status='needs_review';item.summary=e.status||'';}
        }
    });
    return items.reverse();
}
function submissionStatus(item) {
    if(!item)return '';
    if(item.stage) {
        var stages={raw_stored:'Original stored (raw_stored)',parsed:'Read (parsed)',stored:'Saved (stored)',organized_recorded:'Organized (organized_recorded)'};
        var states={pending:'waiting',in_progress:'in progress',completed:'complete',needs_review:'needs review',failed:'failed'};
        return (stages[item.stage]||item.stage)+' · '+(states[item.stage_state]||item.stage_state||'unknown');
    }
    var labels={accepted:'Accepted by relay',dispatching:'Dispatching',handed_off:'Handed off',
        dispatch_uncertain:'Dispatch uncertain',queued_for_device:'Queued for device',needs_review:'Needs review'};
    return labels[item.status]||String(item.status||'Accepted by relay').replace(/_/g,' ');
}
function summary(e) {
    if(e.kind==='agent_reply')return e.message.actor+': '+e.message.text;
    if(e.kind==='stage_update') {
        var names={raw_stored:'Original image saved to Obsidian',parsed:'Read and understood',stored:'Saved to Obsidian',organized_recorded:'Organized in Obsidian'};
        var states={pending:'waiting',in_progress:'in progress',completed:'complete',needs_review:'needs your review',failed:'could not complete'};
        var evidence=e.evidence||{}, parts=[(names[e.stage]||e.stage)+': '+(states[e.state]||e.state)];
        if(evidence.summary)parts.push(evidence.summary);
        if(evidence.reason)parts.push(evidence.reason);
        (evidence.target_notes||[]).forEach(function(n){parts.push(n.title+' — '+n.path);});
        if(evidence.links&&evidence.links.length)parts.push('Linked: '+evidence.links.join(', '));
        return parts.join('\n');
    }
    if(e.kind==='conversation_accepted')return 'Submission to '+e.destination+': '+((e.original||{}).text||'Message received');
    var labels={local_receipt:'Received by relay',capture_accepted:'Selection received',handoff_update:'Delivery update',needs_review:'Needs your review'};
    return (labels[e.kind]||'Update')+': '+(e.status||'received').replace(/_/g,' ');
}
