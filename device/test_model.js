const fs=require('fs'),vm=require('vm'),assert=require('assert');
const m={};vm.createContext(m);vm.runInContext(fs.readFileSync(__dirname+'/Model.js','utf8'),m);
const s=m.fresh(), capture={source:{notebook_id:'book',page_id:'page'},image:{media_type:'image/png',data_base64:'AAAA'}};
let id=m.queue(s,capture,'muse',null,'hello','');
assert.equal(s.outbox[0].payload.content.mode,'raw_image');
assert.equal(s.outbox[0].payload.comment.text,'hello');
assert.throws(()=>m.accept(s,{request_id:'wrong'}));assert.equal(s.outbox.length,1);
const restored=m.restore(JSON.parse(JSON.stringify(s)));
assert.equal(restored.outbox[0].id,id); // retries retain identical id and content
m.accept(s,{request_id:id,status:'dispatched'});assert.equal(s.outbox.length,0);
m.ingest(s,{cursor:2,events:[{cursor:1,kind:'agent_reply'},{cursor:2,kind:'stage_update'}]});
assert.equal(s.cursor,2);const count=s.events.length;
assert.throws(()=>m.ingest(s,{cursor:4,events:[{cursor:4},{cursor:3}]}));
assert.equal(s.events.length,count);assert.equal(s.cursor,2);
assert.throws(()=>m.queue(s,capture,'hermes','','',''));
id=m.queueReply(s,{request_id:'parent',message_id:'answer'},'', 'data:image/png;base64,AAAA');
assert.equal(s.outbox[0].payload.in_reply_to,'answer');assert.equal(s.outbox[0].payload.content.mode,'raw_image');
assert.equal(s.outbox[0].route,4);assert.equal(s.outbox[0].id,id);
assert.throws(()=>m.restore({version:2}));
console.log('Model tests passed: durable retries, receipt identity, cursor ordering, modes, threaded ink reply');

const replyState=m.fresh();
m.queueReply(replyState,{request_id:'original',message_id:'answer'},'A direct reply','');
assert.equal(replyState.outbox[0].payload.content.mode,'raw_text');
assert.equal(replyState.outbox[0].payload.content.text,'A direct reply');
assert.equal(Object.keys(replyState.captures).length,0);
const full=m.fresh();full.events=[{text:'x'.repeat(24*1024*1024)}];full.draft={comment:'keep me'};
assert.throws(()=>m.queue(full,capture,'muse',null,'',''));
assert.equal(full.outbox.length,0);assert.equal(full.draft.comment,'keep me');
assert.equal(Object.keys(full.captures).length,0);
console.log('Capacity and cache-independent direct reply tests passed');

assert.throws(()=>m.queueReply(m.fresh(),{request_id:'r',message_id:'m'},'x'.repeat(16001),''));
assert.throws(()=>m.queue(m.fresh(),capture,'muse',null,'x'.repeat(4001),''));
assert.throws(()=>m.queue(m.fresh(),capture,'muse','x'.repeat(16001),'',''));
assert.throws(()=>m.checkImage({media_type:'image/png',data_base64:'AAAA'.repeat(1400000)},4*1024*1024));
const rejected=m.fresh();const bad=m.queueReply(rejected,{request_id:'r',message_id:'m'},'first','');
m.reject(rejected,bad,'HTTP 400');assert.equal(m.nextItem(rejected),null);assert.equal(rejected.outbox.length,1);
const good=m.queueReply(rejected,{request_id:'r',message_id:'m'},'second','');
assert.equal(m.nextItem(rejected).id,good);m.accept(rejected,{message_id:good,request_id:'r',status:'dispatched'});
assert.equal(rejected.outbox.length,1);assert.equal(rejected.outbox[0].id,bad);
assert.equal(m.sourceLabel({notebook_title:'Ideas',page_label:'2'}),'Ideas · 2');
const label=m.summary({kind:'stage_update',stage:'stored',state:'completed',evidence:{target_notes:[{title:'Notes',path:'Notes.md'}],writer_revision:'hidden'}});
assert(label.includes('Saved to Obsidian'));assert(label.includes('Notes.md'));assert(!label.includes('writer_revision'));
console.log('Payload bounds, rejected-item retention, unblocked outbox, human labels passed');

const bounded=m.fresh();const largeCapture={source:capture.source,image:{media_type:'image/png',data_base64:'AAAA'.repeat(300000)}};
for(let i=0;i<20;i++){const key=m.queue(bounded,largeCapture,'muse',null,'','');m.accept(bounded,{request_id:key,status:'dispatched'});assert(!bounded.captures[key].image);assert(bounded.captures[key].source);}
assert(JSON.stringify(bounded).length<20000);
console.log('Twenty accepted image captures release local base64, preserve source refs, remain bounded');

// Run the actual QML response handler as plain JS to prove a transient send error
// polls inbox immediately and a failed inbox poll does not recurse forever.
const qml=fs.readFileSync(__dirname+'/Main.qml','utf8');
const receiveCode=qml.slice(qml.indexOf('    function outboxItem('),qml.indexOf('    GoodBot.Endpoint {'));
const sent=[],ctx={Model:m,state:m.fresh(),activeItemId:'pending-id',syncing:true,saving:false,dirty:false,loaded:true,
    closeAfterSave:false,notice:'',eventList:{},outboxList:{},outboxCount:0,selectedOutboxId:'',selectedOutbox:null,
    chatList:{},threadList:{},chatCount:0,selectedThreadId:'',threadBlocked:false,pane:'inbox',Qt:{callLater:f=>f()},
    messageText:'',persist:()=>{},setMessageText:value=>ctx.messageText=value,send:(...args)=>sent.push(args)};
const originalId=m.queueReply(ctx.state,{request_id:'r',message_id:'m'},'keep queued','');
const originalPayload=JSON.stringify(ctx.state.outbox[0]);
vm.createContext(ctx);vm.runInContext(receiveCode,ctx);
ctx.receive(9,'HTTP 503: not configured');
assert.deepEqual(sent,[[5,'0']]);assert.equal(ctx.syncing,true);
assert.equal(JSON.stringify(ctx.state.outbox[0]),originalPayload);
ctx.receive(5,JSON.stringify({cursor:1,events:[{cursor:1,kind:'agent_reply'}],has_more:false}));
assert.equal(ctx.state.cursor,1);assert.equal(m.nextItem(ctx.state).id,originalId);
ctx.receive(9,'Offline');assert.equal(sent.length,1);
ctx.syncing=true;ctx.receive(9,'config must be owned by you, mode 600');
assert.equal(ctx.notice,'Agent delivery is not connected yet. Messages are saved in Outbox.');
console.log('Actual QML transient failure handler polls inbox, retains payload/ID, avoids retry recursion');

const chat=m.fresh();chat.draft={directChat:true,comment:'keep my message'};
assert.throws(()=>m.queueConversation(chat,'obsidian','hello'));
assert.throws(()=>m.queueConversation(chat,'muse',' '));
assert.throws(()=>m.queueConversation(chat,'muse','x'.repeat(16001)));
assert.equal(chat.draft.comment,'keep my message');assert.equal(chat.outbox.length,0);
const chatId=m.queueConversation(chat,'muse','Hello Muse');
assert.equal(chat.outbox[0].route,10);assert(!('source' in chat.outbox[0].payload));
assert.equal(Object.keys(chat.captures).length,0);
const reloadedChat=m.restore(JSON.parse(JSON.stringify(chat)));
assert.equal(reloadedChat.outbox[0].id,chatId);
m.accept(chat,{request_id:chatId,status:'handed_off'});assert.equal(chat.outbox.length,0);
assert(m.summary({kind:'conversation_accepted',destination:'muse',original:{text:'Hello'}}).includes('Hello'));
const chatCtx={Model:m,state:m.fresh(),directChat:true,replyTo:null,capture:null,messageText:'Start here',
    ink:{hasInk:false},destination:{currentText:'Muse'},persist:()=>{},setMessageText:value=>chatCtx.messageText=value,
    dismissKeyboard:()=>{},refreshViews:()=>{},unrestoredInk:'',notice:''};
vm.createContext(chatCtx);
vm.runInContext(qml.slice(qml.indexOf('    function submit('),qml.indexOf('    function receive(')),chatCtx);
chatCtx.submit(false);
assert.equal(chatCtx.state.outbox[0].route,10);assert.equal(chatCtx.directChat,false);
const vaultCtx={Model:m,state:m.fresh(),directChat:false,replyTo:null,capture:capture,messageText:'',
    ink:{hasInk:true,dataUrl:()=> 'data:image/png;base64,AAAA'},destination:{currentText:'Muse'},
    converted:{text:''},persist:()=>{},setMessageText:value=>vaultCtx.messageText=value,
    dismissKeyboard:()=>{},refreshViews:()=>{},unrestoredInk:'',notice:''};
vm.createContext(vaultCtx);
vm.runInContext(qml.slice(qml.indexOf('    function submit('),qml.indexOf('    function receive(')),vaultCtx);
vaultCtx.submit(false,'obsidian');
assert.equal(vaultCtx.state.outbox[0].payload.destination,'obsidian');
assert.equal(vaultCtx.state.outbox[0].payload.content.mode,'raw_image');
assert.equal(vaultCtx.state.outbox[0].payload.comment.mode,'ink');
assert.equal(vaultCtx.pane,'outbox');
assert.equal(vaultCtx.notice,'Saving to Outbox; scheduled vault handoff is not connected yet.');
const sourceCtx={state:m.fresh()};vm.createContext(sourceCtx);
vm.runInContext(qml.slice(qml.indexOf('    function sourceFor('),qml.indexOf('    function submit(')),sourceCtx);
assert.equal(sourceCtx.sourceFor({request_id:'chat',source:null}),null);
assert.equal(sourceCtx.sourceFor({request_id:'note',source:{page_id:'page'}}).page_id,'page');
let sourceKeyboardDismissed=0;
const openSourceCtx={confirmSource:null,openConfirm:{visible:false},dismissKeyboard:()=>sourceKeyboardDismissed++};vm.createContext(openSourceCtx);
vm.runInContext(qml.slice(qml.indexOf('    function openSource('),qml.indexOf('    function retryOutbox(')),openSourceCtx);
openSourceCtx.openSource({page_id:'page'});assert.equal(sourceKeyboardDismissed,1);assert.equal(openSourceCtx.openConfirm.visible,true);
console.log('Source-free conversation queue, restart, actual QML submit and absent-source navigation passed');

const encoded={image:capture.image,sha256:'a'.repeat(64)};
const crop=m.selectionCapture(encoded,'book','page','Ideas','2',
    {x:20,y:40,width:80,height:120},{x:-20,y:0,width:400,height:800});
assert.equal(crop.source.crop.x,0.1);assert.equal(crop.source.crop.y,0.05);
assert(Math.abs(crop.source.crop.width-0.2)<1e-12);
assert.equal(crop.source.revision,'render-sha256:'+'a'.repeat(64));
assert.equal(crop.image,encoded.image);assert(crop.source.captured_at.endsWith('Z'));
assert.throws(()=>m.selectionCapture(encoded,'book','page','','',
    {x:-21,y:40,width:80,height:120},{x:-20,y:0,width:400,height:800}));
for(const badBounds of [{x:0,y:0,width:0,height:1},{x:0,y:0,width:Infinity,height:1}])
    assert.throws(()=>m.selectionCapture(encoded,'book','page','','',{x:0,y:0,width:1,height:1},badBounds));
assert.throws(()=>m.selectionCapture({error:'No image'},'book','page','','',{},{}));
assert.throws(()=>m.selectionCapture(encoded,'','page','','',{},{}));
assert.throws(()=>m.selectionCapture({...encoded,sha256:'bad'},'book','page','','',{},{}));
console.log('Native selection identity, image preservation, scene geometry and off-page rejection passed');

let closePrompt=0;
ctx.closeAfterSave=true;ctx.saving=true;ctx.syncing=true;ctx.activeItemId="";ctx.unsavedClose={};
Object.defineProperty(ctx.unsavedClose,'visible',{set:value=>{if(value)closePrompt++;}});
const retained=JSON.stringify(ctx.state);
ctx.receive(9,'config must be owned by you, mode 600');
assert.equal(closePrompt,0);assert.equal(ctx.closeAfterSave,true);assert.equal(ctx.saving,true);
ctx.receive(11,'Snapshot write failed');
assert.equal(closePrompt,1);assert.equal(ctx.closeAfterSave,false);assert.equal(ctx.saving,false);assert.equal(ctx.dirty,true);
assert.equal(JSON.stringify(ctx.state),retained);
ctx.closeAfterSave=true;ctx.saving=true;ctx.dirty=false;ctx.receive(12,'socket closed');
assert.equal(closePrompt,2);assert.equal(ctx.closeAfterSave,false);assert.equal(ctx.saving,false);assert.equal(ctx.dirty,true);
assert.equal(JSON.stringify(ctx.state),retained);
console.log('Delivery errors keep close-save pending; typed save/terminal failures retain state and offer dismissal');

assert.equal(m.sourcePageIndex({page_id:'stable'}, {pageCount:3,pageForId:id=>id==='stable'?2:-1,idForPage:i=>i===2?'stable':'other'}),2);
assert.throws(()=>m.sourcePageIndex({page_id:'deleted'}, {pageCount:3,pageForId:()=>-1,idForPage:()=>{throw Error('must not substitute a page');}}));
assert.throws(()=>m.sourcePageIndex({page_id:'stable'}, {pageCount:3,pageForId:()=>2,idForPage:()=> 'different'}));
assert.throws(()=>m.sourcePageIndex({page_id:'stable'}, null));
console.log('Exact source-page lookup rejects missing pages and mismatched reverse IDs');

assert(!/\bKeyboard\s*\{/.test(qml));
assert(qml.includes('Qt.inputMethod.hide()'));
assert(qml.includes('Qt.inputMethod.keyboardRectangle.height'));
assert(qml.includes('GoodBot.InkPad'));
assert(/Layout\.bottomMargin:\s*root\.keyboardInset/.test(qml));
assert.equal(ctx.deliveryNotice('config must be owned by you, mode 600',false),
    'Agent delivery is not connected yet. Messages are saved in Outbox.');
assert.equal(ctx.deliveryNotice('TLS connection failed',true),
    'Agent delivery is not connected yet. Messages are saved in Outbox.');
assert.equal(ctx.deliveryNotice('Offline',false),'Offline');
assert.equal(ctx.deliveryNotice('Snapshot write failed',false),'Snapshot write failed');
console.log('Native keyboard-only compose layout and delivery error redaction passed');

const retryState=m.fresh();const retryId=m.queueConversation(retryState,'muse','Exact retained message');
const retrySent=[],retryCtx={Model:m,state:retryState,loaded:true,saving:false,dirty:false,syncing:false,
    activeItemId:'',notice:'',send:(...args)=>retrySent.push(args)};
vm.createContext(retryCtx);
vm.runInContext(qml.slice(qml.indexOf('    function outboxItem('),qml.indexOf('    function submit(')),retryCtx);
const retrySnapshot=JSON.stringify(retryState.outbox[0]);
retryCtx.retryOutbox(retryId);
assert.deepEqual(retrySent,[[10,JSON.stringify(retryState.outbox[0].payload)]]);
assert.equal(retryCtx.activeItemId,retryId);assert.equal(JSON.stringify(retryState.outbox[0]),retrySnapshot);
assert.equal(retryCtx.outboxDestination(retryState.outbox[0]),'muse');
assert.equal(retryCtx.outboxText(retryState.outbox[0]),'Exact retained message');
assert.equal(retryCtx.outboxStatus(vaultCtx.state.outbox[0]),'Queued; vault processing not connected');
retryCtx.syncing=false;m.reject(retryState,retryId,'HTTP 400');retryCtx.retryOutbox(retryId);
assert.equal(retrySent.length,1);assert.equal(retryState.outbox[0].id,retryId);
console.log('Outbox preview and manual retry retain exact queued ID/payload and reject unsafe retries');

const oldDraft={capture:{source:{page_id:'old'},image:capture.image},comment:'keep',converted:'',destination:0,ink:'',reply:null,directChat:false};
ctx.state=m.fresh();ctx.state.draft=oldDraft;ctx.capture=oldDraft.capture;ctx.replyTo=null;ctx.directChat=false;
ctx.receive(2,JSON.stringify({source:{page_id:'new'},image:capture.image}));
assert.equal(ctx.capture.source.page_id,'old');assert.equal(ctx.state.draft,oldDraft);
assert(ctx.notice.includes('current draft'));
const draftCtx={capture:oldDraft.capture,replyTo:null,directChat:false,restoring:false,state:{draft:{ink:'previous'}},pane:'compose',selectedThreadId:'',threadDraftId:'',messageText:'keep',
    converted:{text:''},destination:{currentIndex:0},draftSave:{restart:()=>assert.fail('must not save empty drawing')},
    ink:{hasInk:true,dataUrl:()=>'',error:'PNG encoding failed'},unrestoredInk:'',notice:''};
vm.createContext(draftCtx);
vm.runInContext(qml.slice(qml.indexOf('    function draft('),qml.indexOf('    function sourceFor(')),draftCtx);
assert.equal(draftCtx.draft(),false);assert.equal(draftCtx.state.draft.ink,'previous');assert.equal(draftCtx.notice,'PNG encoding failed');

const legacyInk='data:image/png;base64,OLD';
const inkCtx={Model:m,capture:oldDraft.capture,replyTo:null,directChat:false,restoring:false,state:m.fresh(),pane:'compose',selectedThreadId:'',threadDraftId:'',messageText:'keep',
    converted:{text:''},destination:{currentIndex:0,currentText:'Muse'},draftSave:{restart:()=>{}},setMessageText:value=>inkCtx.messageText=value,
    ink:{hasInk:true,dataUrl:()=>assert.fail('unrestored ink must not be replaced')},unrestoredInk:legacyInk,notice:'',dismissKeyboard:()=>{},refreshViews:()=>{},persist:()=>{}};
vm.createContext(inkCtx);
vm.runInContext(qml.slice(qml.indexOf('    function draft('),qml.indexOf('    function sourceFor(')),inkCtx);
vm.runInContext(qml.slice(qml.indexOf('    function submit('),qml.indexOf('    function receive(')),inkCtx);
assert.equal(inkCtx.draft(),true);assert.equal(inkCtx.state.draft.ink,legacyInk);
inkCtx.submit(false);assert.equal(inkCtx.state.outbox.length,0);assert(inkCtx.notice.includes('Clear it explicitly'));

let sizePrompt=0;
const sizeClosePrompt={};Object.defineProperty(sizeClosePrompt,'visible',{set:value=>{if(value)sizePrompt++;}});
const sizeCtx={loaded:true,dirty:false,saving:false,closeAfterSave:true,
    state:{text:'x'.repeat(24*1024*1024)},notice:'',unsavedClose:sizeClosePrompt,send:()=>assert.fail('oversize state must not send')};
vm.createContext(sizeCtx);
vm.runInContext(qml.slice(qml.indexOf('    function persist('),qml.indexOf('    function sync(')),sizeCtx);
sizeCtx.persist();assert.equal(sizePrompt,1);assert.equal(sizeCtx.closeAfterSave,false);assert.equal(sizeCtx.dirty,true);
console.log('New captures, drawing failures, unreadable legacy ink, and local size failures preserve user state');

const history=m.fresh();const historyId=m.queueConversation(history,'muse','First turn');
let turns=m.threadMessages(history,historyId);
assert.equal(turns.length,1);assert.equal(turns[0].text,'First turn');assert.equal(turns[0].status,'Queued');
assert.equal(m.chatThreads(history)[0].request_id,historyId);assert(m.threadPending(history,historyId));
m.accept(history,{request_id:historyId,status:'handed_off'});
m.ingest(history,{cursor:2,events:[
    {cursor:1,kind:'conversation_accepted',request_id:historyId,destination:'muse',source:null,original:{mode:'raw_text',text:'First turn'}},
    {cursor:2,kind:'agent_reply',message:{message_id:'agent-1',request_id:historyId,in_reply_to:historyId,actor:'muse',text:'Reply'}}
]});
turns=m.threadMessages(history,historyId);
assert.deepEqual(turns.map(turn=>turn.message_id),[historyId,'agent-1']);
assert.equal(turns[1].in_reply_to,historyId);assert.equal(m.replyTarget(history,historyId).message_id,'agent-1');
const followup=m.queueReply(history,m.replyTarget(history,historyId),'Second turn','');
turns=m.threadMessages(history,historyId);assert.equal(turns[2].message_id,followup);assert.equal(turns[2].status,'Queued');
m.accept(history,{message_id:followup,request_id:historyId,status:'handed_off'});
turns=m.threadMessages(history,historyId);assert.equal(turns[2].text,'Second turn');assert.equal(turns[2].in_reply_to,'agent-1');
assert.equal(m.replyTarget(history,historyId).message_id,followup);assert.equal(m.inboxEvents(history).length,2);
assert(!JSON.stringify(bounded.events).includes(largeCapture.image.data_base64));
console.log('Chronological chat threads retain turn identity, dedupe acceptance, show queued turns, and exclude chat traffic from Inbox');

const race=m.fresh();race.events=[
    {kind:'conversation_accepted',request_id:'race',destination:'muse',source:null,original:{mode:'raw_text',text:'Start'},created_at:'2026-01-01T00:00:00Z'},
    {kind:'agent_reply',message:{message_id:'agent-first',request_id:'race',in_reply_to:'race',actor:'muse',text:'First reply',created_at:'2026-01-01T00:01:00Z'}}
];
const racedUser=m.queueReply(race,{request_id:'race',message_id:'agent-first'},'Device follow-up','');
race.outbox[0].created_at='2026-01-01T00:02:00Z';
m.ingest(race,{cursor:1,events:[{cursor:1,kind:'agent_reply',message:{message_id:'agent-later',request_id:'race',in_reply_to:racedUser,actor:'muse',text:'Later reply',created_at:'2026-01-01T00:03:00Z'}}]});
turns=m.threadMessages(race,'race');
assert.deepEqual(turns.map(turn=>turn.message_id),['race','agent-first',racedUser,'agent-later']);
assert.equal(turns[2].status,'Queued');assert.equal(m.replyTarget(race,'race').message_id,'agent-later');
m.accept(race,{message_id:racedUser,request_id:'race',status:'handed_off'});
assert.deepEqual(m.threadMessages(race,'race').map(turn=>turn.message_id),['race','agent-first',racedUser,'agent-later']);
assert.equal(m.replyTarget(race,'race').message_id,'agent-later');

function causalRace(agentTime,suffix) {
    const state=m.fresh(),request='causal-'+suffix,first='first-'+suffix;
    state.events=[
        {kind:'conversation_accepted',request_id:request,destination:'muse',source:null,original:{mode:'raw_text',text:'Start'},created_at:'2025-12-31T22:00:00+00:00'},
        {kind:'agent_reply',message:{message_id:first,request_id:request,in_reply_to:request,actor:'muse',text:'First',created_at:'2025-12-31T22:01:00+00:00'}}
    ];
    const user=m.queueReply(state,{request_id:request,message_id:first},'Follow-up','');
    state.outbox[0].created_at='2026-01-01T00:00:00.900Z';
    state.events.push({kind:'agent_reply',message:{message_id:'later-'+suffix,request_id:request,in_reply_to:user,actor:'muse',text:'Later',created_at:agentTime}});
    const ids=m.threadMessages(state,request).map(turn=>turn.message_id);
    assert(ids.indexOf(user)<ids.indexOf('later-'+suffix));
    assert.equal(m.replyTarget(state,request).message_id,'later-'+suffix);
}
causalRace('2026-01-01T00:00:00+00:00','mixed-precision');
causalRace('2025-12-31T21:00:00+00:00','clock-skew');

const pendingDuplicate=m.fresh();const pendingId=m.queueConversation(pendingDuplicate,'muse','Receipt pending');
pendingDuplicate.events.push({kind:'conversation_accepted',request_id:pendingId,destination:'muse',source:null,
    original:{mode:'raw_text',text:'Receipt pending'},created_at:pendingDuplicate.outbox[0].created_at});
assert.equal(m.threadMessages(pendingDuplicate,pendingId).length,1);
assert(m.threadMessages(pendingDuplicate,pendingId)[0].pending);assert(m.chatThreads(pendingDuplicate)[0].pending);

const legacyOrder=m.fresh();legacyOrder.events=[
    {kind:'device_message',request_id:'legacy',message_id:'known-before',content:{mode:'raw_text',text:'Known'},created_at:'2026-01-01T00:03:00Z'},
    {kind:'device_message',request_id:'legacy',message_id:'unknown',content:{mode:'raw_text',text:'Legacy'}},
    {kind:'agent_reply',message:{message_id:'known-after',request_id:'legacy',actor:'muse',text:'After',created_at:'2026-01-01T00:01:00Z'}}
];
assert.deepEqual(m.threadMessages(legacyOrder,'legacy').map(turn=>turn.message_id),['known-before','unknown','known-after']);
const cyclic=m.fresh();cyclic.events=[
    {kind:'device_message',request_id:'cycle',message_id:'cycle-a',in_reply_to:'cycle-b',content:{mode:'raw_text',text:'A'},created_at:'2026-01-01T00:00:00Z'},
    {kind:'device_message',request_id:'cycle',message_id:'cycle-b',in_reply_to:'cycle-a',content:{mode:'raw_text',text:'B'},created_at:'2026-01-01T00:01:00Z'}
];
assert.deepEqual(m.threadMessages(cyclic,'cycle').map(turn=>turn.message_id),['cycle-a','cycle-b']);
const reversed=m.fresh();reversed.events=[
    {kind:'agent_reply',message:{message_id:'chain-e',request_id:'chain',in_reply_to:'chain-d',actor:'muse',text:'E',created_at:'2026-01-01T00:00:00Z'}},
    {kind:'agent_reply',message:{message_id:'chain-d',request_id:'chain',in_reply_to:'chain-c',actor:'muse',text:'D',created_at:'2026-01-01T00:01:00Z'}},
    {kind:'agent_reply',message:{message_id:'chain-c',request_id:'chain',in_reply_to:'chain-b',actor:'muse',text:'C',created_at:'2026-01-01T00:02:00Z'}},
    {kind:'agent_reply',message:{message_id:'chain-b',request_id:'chain',in_reply_to:'chain-a',actor:'muse',text:'B',created_at:'2026-01-01T00:03:00Z'}},
    {kind:'device_message',request_id:'chain',message_id:'chain-a',content:{mode:'raw_text',text:'A'},created_at:'2026-01-01T00:04:00Z'}
];
assert.deepEqual(m.threadMessages(reversed,'chain').map(turn=>turn.message_id),['chain-a','chain-b','chain-c','chain-d','chain-e']);
console.log('Receipt races honor causal parents across mixed precision and clock skew, keep pending badges, and preserve legacy/cyclic fallback order');

const sessionQml=fs.readFileSync(__dirname+'/native/Session.qml','utf8');
const wholeEncoded={sha256:'a'.repeat(64),image:{media_type:'image/png',data_base64:'AAAA'}};
const pageHandoff=m.pageCapture(wholeEncoded,'book','page','Whole notebook','2');
assert.equal(pageHandoff.source.selection,'page');assert(!('crop' in pageHandoff.source));
assert.equal(pageHandoff.image.data_base64,'AAAA');assert.equal(pageHandoff.source.page_id,'page');
const layout=m.pageLayout({x:0,y:0,width:1000,height:1500},{x:-100,y:0,width:1200,height:4000});
assert.equal(layout.bounds.x,-100);assert.equal(layout.bounds.height,4000);
assert(layout.width<=2048&&layout.height<=8192&&layout.width*layout.height<=16*1024*1024);
assert.equal(m.pageLayout({x:0,y:0,width:1000,height:1500},{x:0,y:0,width:0,height:0}).height,1500);
assert.throws(()=>m.pageLayout({x:0,y:0,width:0,height:0},{}));
assert.throws(()=>m.pageLayout({x:0,y:0,width:1000,height:1000000},{x:0,y:0,width:1000,height:1000000}));
let resumes=0,renderCount=0,callback,errorCallback;
const vaultSubmissions=[];
const shareClient={loaded:false,hasActiveDraft:false,pane:'chats',notice:'',capture:null,
    nativeEndpoint:{encodeImage:()=>wholeEncoded},resumeDraft:()=>resumes++,
    receive:(type,body)=>{assert.equal(type,2);shareClient.capture=JSON.parse(body);},
    submit:(...args)=>vaultSubmissions.push(args)};
const provider={goodbotCapturePage:(success,failure)=>{renderCount++;callback=success;errorCallback=failure;}};
const shareContext={Model:m,capturingPage:false,panel:{client:shareClient,showPopup:()=>{}},console:{log:()=>{}}};
vm.createContext(shareContext);
vm.runInContext(sessionQml.slice(sessionQml.indexOf('    function openSend('),sessionQml.indexOf('    function capture(')),shareContext);
shareContext.openSend(provider);assert(shareClient.notice.includes('Still loading'));assert.equal(renderCount,0);
shareClient.loaded=true;shareClient.hasActiveDraft=true;
shareContext.openSend(provider);assert.equal(resumes,1);assert.equal(renderCount,0);
shareContext.openVault(provider);assert(shareClient.notice.includes('Finish or send'));assert.equal(vaultSubmissions.length,0);
shareClient.hasActiveDraft=false;
shareContext.openSend(provider);assert.equal(renderCount,1);assert.equal(shareContext.capturingPage,true);
shareContext.openSend(provider);assert.equal(renderCount,1);
callback({},'book','page','Whole notebook','2');assert.equal(shareContext.capturingPage,false);
assert.equal(shareClient.capture.source.selection,'page');assert.equal(vaultSubmissions.length,0);
shareClient.capture=null;
shareContext.openVault(provider);callback({},'book','page','Whole notebook','2');
assert.deepEqual(vaultSubmissions,[[false,'obsidian']]);
shareClient.capture=null;
shareContext.openSend(provider);errorCallback('render failed');assert.equal(shareContext.capturingPage,false);assert.equal(shareClient.notice,'render failed');
callback({},'book','other','Other','3');assert.equal(shareClient.capture,null);
shareContext.openSend(null);assert(shareClient.notice.includes('not ready'));
shareContext.openSend(provider);shareClient.hasActiveDraft=true;
callback({},'book','page','Whole notebook','2');assert.equal(shareClient.capture,null);assert.equal(shareContext.capturingPage,false);
assert(shareClient.notice.includes('existing draft was preserved'));
shareClient.capture=capture;shareClient.directChat=false;shareClient.replyTo=null;
shareContext.openVault(provider);assert.equal(vaultSubmissions.length,2);assert.equal(renderCount,4);
console.log('Full-page geometry, exact identity, Share rendering, direct vault queue, cancellation and draft protection passed');

shareClient.capture=null;shareClient.hasActiveDraft=false;
shareContext.openSend(provider);
callback({},'book','page','Selection','2',{x:10,y:20,width:100,height:50},{x:0,y:0,width:1000,height:1500});
assert.equal(shareClient.capture.source.selection,'crop');
assert.equal(shareClient.capture.source.crop.x,0.01);
assert(Math.abs(shareClient.capture.source.crop.height-50/1500)<1e-12);
console.log('Share preserves active lasso crop identity through the same preview/send workflow');
