// Pure shared model: executable by QML and the host Node regression test.
function fresh() { return {version:1,cursor:0,outbox:[],events:[],captures:{},draft:null}; }
function restore(value) {
    if (!value || Object.keys(value).length === 0) return fresh();
    if (value.version !== 1 || !Array.isArray(value.outbox) || !Array.isArray(value.events)
            || typeof value.cursor !== 'number' || !value.captures) throw Error('Unsupported saved state; preserved on disk');
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
    var next={version:state.version,cursor:state.cursor,outbox:state.outbox.concat([item]),events:state.events,captures:captures,draft:keepDraft?state.draft:null};
    if(unescape(encodeURIComponent(JSON.stringify(next))).length>24*1024*1024)
        throw Error('Local storage limit reached; draft preserved. Export/archive state before adding captures');
    state.outbox=next.outbox;state.captures=captures;state.draft=next.draft;
    return item.id;
}
function queueReply(state, reply, text, ink) {
    if(!reply||!reply.request_id||!reply.message_id)throw Error('Reply thread required');
    if(ink&&text.trim())throw Error('Choose typed OR drawn reply');
    if(!ink&&!text.trim())throw Error('Write or draw your reply first');
    if(text.length>16000)throw Error('Reply must be at most 16,000 characters');
    if(ink)checkImage(imageFromUrl(ink),4*1024*1024);
    var id=uid(),content=ink?{mode:'raw_image',image:imageFromUrl(ink)}:{mode:'raw_text',text:text};
    return enqueue(state,{id:id,route:4,created_at:new Date().toISOString(),payload:{message_id:id,request_id:reply.request_id,in_reply_to:reply.message_id,content:content}},null);
}
function queueConversation(state, destination, text) {
    if(['muse','hermes','codex'].indexOf(destination)<0)throw Error('Choose an agent');
    if(typeof text!=='string'||!text.trim()||text.length>16000)throw Error('Write a message of at most 16,000 characters');
    var id=uid();
    return enqueue(state,{id:id,route:10,created_at:new Date().toISOString(),payload:{request_id:id,destination:destination,content:{mode:'raw_text',text:text}}},null);
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
        status:receipt.status,created_at:item.created_at||new Date().toISOString()});
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
function sourcePageIndex(source, entry) {
    if(!source||!entry||typeof entry.pageForId!=='function'||typeof entry.idForPage!=='function')
        throw Error('Source notebook is unavailable');
    var index=entry.pageForId(source.page_id);
    if(typeof index!=='number'||!isFinite(index)||Math.floor(index)!==index||index<0||index>=entry.pageCount
            ||String(entry.idForPage(index))!==source.page_id)
        throw Error('Source page is unavailable; no other page was opened');
    return index;
}
function _turnText(content, comment, selection) {
    content=content||{};comment=comment||{};
    var text=content.mode==='raw_text'?content.text||'':content.mode==='converted_text'?(content.text||content.converted_text||''):(selection?'Notebook selection':'Handwritten reply');
    if(comment.mode==='text'&&comment.text)text+=(text?'\n':'')+comment.text;
    else if(comment.mode==='ink')text+=(text?'\n':'')+'Handwritten comment';
    return text||'Message';
}
function threadMessages(state, requestId) {
    var messages=[],seen={},order=0;
    function timestamp(value) {
        if(!value)return null;
        var parsed=Date.parse(value);return isFinite(parsed)?parsed:null;
    }
    function add(message) {
        if(!message.message_id)return;
        var key='@'+message.message_id,existing=seen[key];
        if(existing) {
            if(message.detail) {
                existing.text=message.text;existing.source=message.source||existing.source;
                existing.preview_request_id=message.preview_request_id;existing.image=message.image||'';
                existing.comment_image=message.comment_image||'';
            }
            if(message.pending) {existing.pending=true;existing.status=message.status;}
            if(timestamp(message.created_at)!==null&&(timestamp(existing.created_at)===null||timestamp(message.created_at)<timestamp(existing.created_at)))
                existing.created_at=message.created_at;
            return;
        }
        message._order=order++;seen[key]=message;messages.push(message);
    }
    state.events.forEach(function(e){
        if(e.kind==='device_message'&&e.request_id===requestId) {
            add({role:'user',actor:'You',request_id:requestId,message_id:e.message_id,in_reply_to:e.in_reply_to,
                text:_turnText(e.content,e.comment,!!e.source),source:e.source||null,
                preview_request_id:e.source&&e.content&&e.content.mode!=='raw_text'?requestId:'',status:e.status||'Sent',
                created_at:e.created_at||'',detail:true,pending:false});
        } else if((e.kind==='conversation_accepted'||e.kind==='capture_accepted')&&e.request_id===requestId) {
            var original=e.original||{};
            add({role:'user',actor:'You',request_id:requestId,message_id:requestId,in_reply_to:null,
                text:_turnText(original,null,!!e.source),source:e.source||null,
                preview_request_id:original.mode==='raw_text'?'':requestId,status:'Sent',created_at:e.created_at||'',pending:false});
        } else if(e.kind==='agent_reply'&&e.message&&e.message.request_id===requestId) {
            add({role:'agent',actor:e.message.actor||'Agent',request_id:requestId,message_id:e.message.message_id,
                in_reply_to:e.message.in_reply_to||requestId,text:e.message.text||'',source:e.source||null,
                preview_request_id:'',status:'',created_at:e.message.created_at||e.created_at||'',pending:false});
        }
    });
    state.outbox.forEach(function(item){
        var payload=item.payload||{};
        if(payload.request_id!==requestId)return;
        var content=payload.content||{},comment=payload.comment||{};
        add({role:'user',actor:'You',request_id:requestId,message_id:payload.message_id||item.id,
            in_reply_to:payload.in_reply_to||null,text:_turnText(content,comment,!!payload.source),source:payload.source||null,
            image:imageUrl(content.image||content.source_image||null),comment_image:comment.mode==='ink'?imageUrl(comment.image):'',
            preview_request_id:'',status:item.status==='needs_review'?'Needs review':'Queued',created_at:item.created_at||'',detail:true,pending:true});
    });
    // Unknown timestamps from restored legacy state remain fixed. Sort only the
    // contiguous dated runs around them so no ordering is invented for old turns.
    for(var start=0;start<messages.length;) {
        messages[start]._time=timestamp(messages[start].created_at);
        if(messages[start]._time===null){start++;continue;}
        var end=start+1;
        while(end<messages.length) {
            messages[end]._time=timestamp(messages[end].created_at);
            if(messages[end]._time===null)break;
            end++;
        }
        var run=messages.slice(start,end).sort(function(a,b){
            return a._time===b._time?a._order-b._order:a._time-b._time;
        });
        for(var at=0;at<run.length;at++)messages[start+at]=run[at];
        start=end;
    }
    // Message ancestry is authoritative when tablet and relay clocks differ or
    // relay timestamps lose sub-second precision. Stable topological emission
    // keeps unrelated turns in time order and falls back unchanged for a cycle.
    var fallback=messages.slice(),remaining=messages.slice(),ordered=[],known={},emitted={};
    messages.forEach(function(message){known['@'+message.message_id]=true;});
    while(remaining.length) {
        var ready=-1;
        for(var candidate=0;candidate<remaining.length;candidate++) {
            var parentId=remaining[candidate].in_reply_to;
            if(!parentId||!known['@'+parentId]||emitted['@'+parentId]){ready=candidate;break;}
        }
        if(ready<0){ordered=fallback;break;}
        var next=remaining.splice(ready,1)[0];ordered.push(next);emitted['@'+next.message_id]=true;
    }
    messages=ordered;
    messages.forEach(function(message){delete message._order;delete message._time;delete message.detail;});
    return messages;
}
function chatThreads(state) {
    var ids=[],info={};
    function touch(id,destination,source) {
        if(!id)return;
        var old=ids.indexOf(id);if(old>=0)ids.splice(old,1);ids.push(id);
        if(!info[id])info[id]={request_id:id,destination:'',source:null};
        if(destination)info[id].destination=destination;
        if(source)info[id].source=source;
    }
    state.events.forEach(function(e){
        if(e.kind==='conversation_accepted'||e.kind==='capture_accepted'||e.kind==='device_message')
            touch(e.request_id,e.destination,e.source);
        else if(e.kind==='agent_reply'&&e.message)touch(e.message.request_id,e.message.actor,e.source);
    });
    state.outbox.forEach(function(item){var p=item.payload||{};touch(p.request_id,p.destination,p.source);});
    return ids.reverse().map(function(id){
        var messages=threadMessages(state,id),last=messages.length?messages[messages.length-1]:null,meta=info[id];
        return {request_id:id,destination:meta.destination||'agent',source:meta.source,
            preview:last?last.text:'Conversation',turns:messages.length,pending:messages.some(function(m){return m.pending;})};
    });
}
function threadPending(state, requestId) {
    return state.outbox.some(function(item){return item.payload&&item.payload.request_id===requestId;});
}
function replyTarget(state, requestId) {
    var messages=threadMessages(state,requestId);
    for(var i=messages.length-1;i>=0;i--)if(!messages[i].pending&&messages[i].message_id)
        return {request_id:requestId,message_id:messages[i].message_id};
    return null;
}
function inboxEvents(state) {
    return state.events.filter(function(e){return ['conversation_accepted','capture_accepted','agent_reply','device_message'].indexOf(e.kind)<0;});
}
function summary(e) {
    if(e.kind==='agent_reply')return e.message.actor+': '+e.message.text;
    if(e.kind==='stage_update') {
        var names={parsed:'Read and understood',stored:'Saved to Obsidian',organized_recorded:'Organized in Obsidian'};
        var states={pending:'waiting',in_progress:'in progress',completed:'complete',needs_review:'needs your review',failed:'could not complete'};
        var evidence=e.evidence||{}, parts=[(names[e.stage]||e.stage)+': '+(states[e.state]||e.state)];
        if(evidence.summary)parts.push(evidence.summary);
        if(evidence.reason)parts.push(evidence.reason);
        (evidence.target_notes||[]).forEach(function(n){parts.push(n.title+' — '+n.path);});
        if(evidence.links&&evidence.links.length)parts.push('Linked: '+evidence.links.join(', '));
        return parts.join('\n');
    }
    if(e.kind==='conversation_accepted')return 'Chat with '+e.destination+': '+((e.original||{}).text||'Message received');
    var labels={local_receipt:'Received by relay',capture_accepted:'Selection received',handoff_update:'Delivery update',needs_review:'Needs your review'};
    return (labels[e.kind]||'Update')+': '+(e.status||'received').replace(/_/g,' ');
}
