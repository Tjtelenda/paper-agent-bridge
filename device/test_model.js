const fs=require('fs'),vm=require('vm'),assert=require('assert');
const m={};vm.createContext(m);vm.runInContext(fs.readFileSync(__dirname+'/Model.js','utf8'),m);
const qml=fs.readFileSync(__dirname+'/Main.qml','utf8');
const capture={source:{notebook_id:'book',page_id:'page',notebook_title:'Ideas',page_label:'2'},image:{media_type:'image/png',data_base64:'AAAA'}};
let legacySequence=0;
function enqueueLegacy(state,text,route=10) {
    const id='legacy-submission-'+(++legacySequence),request=route===4?'legacy-thread':id;
    const payload={request_id:request,content:{mode:'raw_text',text}};
    if(route===4){payload.message_id=id;payload.in_reply_to='legacy-parent';}
    else payload.destination='muse';
    return m.enqueue(state,{id,route,created_at:'2026-10-19T10:00:00Z',payload},null);
}

const state=m.fresh();
let id=m.queue(state,capture,'muse',null,'hello','');
assert.equal(state.outbox[0].payload.content.mode,'raw_image');
assert.equal(state.outbox[0].payload.comment.text,'hello');
assert.throws(()=>m.accept(state,{request_id:'wrong'}));assert.equal(state.outbox.length,1);
assert.equal(m.restore(JSON.parse(JSON.stringify(state))).outbox[0].id,id);
m.accept(state,{request_id:id,status:'handed_off'});assert.equal(state.outbox.length,0);
let accepted=m.historySubmissions(state);
assert.equal(accepted.length,1);assert.equal(accepted[0].id,id);
assert.equal(m.submissionStatus(accepted[0]),'Handed off');assert.equal(accepted[0].source.page_id,'page');
m.ingest(state,{cursor:2,events:[{cursor:1,kind:'agent_reply',message:{message_id:'answer',request_id:id,actor:'muse',text:'Use the normal channel'}},{cursor:2,kind:'stage_update',request_id:id,stage:'parsed',state:'in_progress',evidence:{summary:'Reading'}}]});
assert.equal(state.cursor,2);assert.equal(m.historySubmissions(state).length,1);
assert.equal(m.submissionStatus(m.historySubmissions(state)[0]),'Read (parsed) · in progress');
assert.throws(()=>m.ingest(state,{cursor:4,events:[{cursor:4},{cursor:3}]}));assert.equal(state.cursor,2);
console.log('Queued capture acceptance becomes source-capable History with truthful receipt and stage status');

assert.throws(()=>m.queue(m.fresh(),capture,'hermes','','',''));
assert.throws(()=>m.queue(m.fresh(),capture,'muse',null,'x'.repeat(4001),''));
assert.throws(()=>m.queue(m.fresh(),capture,'muse','x'.repeat(16001),'',''));
assert.throws(()=>m.checkImage({media_type:'image/png',data_base64:'AAAA'.repeat(1400000)},4*1024*1024));
const rejected=m.fresh(),bad=enqueueLegacy(rejected,'first');
m.reject(rejected,bad,'HTTP 400');assert.equal(m.nextItem(rejected),null);assert.equal(rejected.outbox.length,1);
const good=m.queue(rejected,capture,'muse',null,'','');
assert.equal(m.nextItem(rejected).id,good);m.accept(rejected,{request_id:good,status:'handed_off'});
assert.equal(rejected.outbox.length,1);assert.equal(rejected.outbox[0].id,bad);
console.log('Payload bounds and rejected-item retention keep the remaining queue usable');

const bounded=m.fresh(),largeCapture={source:capture.source,image:{media_type:'image/png',data_base64:'AAAA'.repeat(300000)}};
for(let i=0;i<20;i++){const key=m.queue(bounded,largeCapture,'muse',null,'','');m.accept(bounded,{request_id:key,status:'handed_off'});assert(!bounded.captures[key].image);assert(bounded.captures[key].source);}
assert(JSON.stringify(bounded).length<30000);assert(!JSON.stringify(bounded.events).includes(largeCapture.image.data_base64));
console.log('Accepted captures release local base64 while retaining source identity');

const receiveCode=qml.slice(qml.indexOf('    function outboxItem('),qml.indexOf('    GoodBot.Endpoint {'));
const sent=[],ctx={vaultSavePending:false,Model:m,state:m.fresh(),activeItemId:'pending-id',syncing:true,saving:false,dirty:false,loaded:true,
    closeAfterSave:false,notice:'',queuedList:{model:[]},historyList:{model:[]},queuedCount:0,historyCount:0,
    selectedSubmissionId:'',selectedSubmission:null,selectedQueued:false,pane:'queued',sourcePreview:'',previewRequestId:'',
    messageText:'',persist:()=>{},setMessageText:value=>ctx.messageText=value,send:(...args)=>sent.push(args)};
const queuedLegacy=enqueueLegacy(ctx.state,'Retained legacy message');
const queuedSnapshot=JSON.stringify(ctx.state.outbox[0]);
vm.createContext(ctx);vm.runInContext(receiveCode,ctx);
ctx.receive(9,'HTTP 503: not configured');
assert.deepEqual(sent,[[5,'0']]);assert.equal(ctx.syncing,true);assert.equal(JSON.stringify(ctx.state.outbox[0]),queuedSnapshot);
ctx.receive(5,JSON.stringify({cursor:1,events:[{cursor:1,kind:'agent_reply',message:{message_id:'old-answer',request_id:queuedLegacy,actor:'muse',text:'Old reply'}}],has_more:false}));
assert.equal(ctx.state.cursor,1);assert.equal(m.nextItem(ctx.state).id,queuedLegacy);
ctx.syncing=true;ctx.receive(9,'config must be owned by you, mode 600');
assert.equal(ctx.notice,'Agent delivery is not connected yet. Submissions remain in Queued.');
ctx.syncing=false;ctx.selectedQueued=true;ctx.selectedSubmissionId=queuedLegacy;ctx.selectedSubmission=ctx.state.outbox[0];ctx.pane='detail';
ctx.receive(10,JSON.stringify({request_id:queuedLegacy,status:'handed_off'}));
assert.equal(ctx.pane,'detail');assert.equal(ctx.selectedQueued,false);assert.equal(ctx.selectedSubmission.id,queuedLegacy);
console.log('Transient delivery failures retain exact legacy queued payloads and poll status once');

const legacy=m.fresh(),legacyDraft={directChat:true,comment:'keep my unsent message'};
const legacyId=enqueueLegacy(legacy,'Hello Muse');legacy.draft=legacyDraft;
assert.strictEqual(legacy.draft,legacyDraft);assert.equal(legacy.outbox[0].id,legacyId);
m.accept(legacy,{request_id:legacyId,status:'dispatch_uncertain'});
legacy.events.push({kind:'conversation_accepted',request_id:legacyId,destination:'muse',source:null,original:{mode:'raw_text',text:'Hello Muse'}});
accepted=m.historySubmissions(legacy);
assert.equal(accepted.length,1);assert.equal(accepted[0].content.text,'Hello Muse');
assert.equal(m.submissionStatus(accepted[0]),'Dispatch uncertain');assert.strictEqual(legacy.draft,legacyDraft);
assert(!qml.includes('New chat'));assert(!qml.includes('Reply to this conversation'));
assert(qml.includes('model: ["Queued"'));assert(qml.includes('"History"'));
console.log('Legacy source-free submissions remain readable in Queued/History while chat creation is absent');

const encoded={image:capture.image,sha256:'a'.repeat(64)};
const crop=m.selectionCapture(encoded,'book','page','Ideas','2',{x:20,y:40,width:80,height:120},{x:-20,y:0,width:400,height:800});
assert.equal(crop.source.crop.x,0.1);assert.equal(crop.source.crop.y,0.05);assert(Math.abs(crop.source.crop.width-0.2)<1e-12);
assert.equal(crop.source.revision,'render-sha256:'+'a'.repeat(64));
assert.throws(()=>m.selectionCapture(encoded,'book','page','','',{x:-21,y:40,width:80,height:120},{x:-20,y:0,width:400,height:800}));
const page=m.pageCapture(encoded,'book','page','Ideas','2');assert.equal(page.source.selection,'page');assert(!('crop' in page.source));
const layout=m.pageLayout({x:0,y:0,width:1000,height:1500},{x:-100,y:0,width:1200,height:4000});
assert(layout.width<=2048&&layout.height<=8192&&layout.width*layout.height<=16*1024*1024);
assert.equal(m.sourcePageIndex({page_id:'stable'},{pageCount:3,pageForId:x=>x==='stable'?2:-1,idForPage:i=>i===2?'stable':'other'}),2);
assert.throws(()=>m.sourcePageIndex({page_id:'deleted'},{pageCount:3,pageForId:()=>-1,idForPage:()=>''}));
console.log('Selection/page captures preserve exact source identity and bounded geometry');

assert(!/\bKeyboard\s*\{/.test(qml));assert(!qml.includes('text: "Hide keyboard"'));
assert(qml.includes('Qt.inputMethod.hide()'));assert(qml.includes('Qt.inputMethod.keyboardRectangle.height'));
assert(qml.includes('TapHandler'));assert(qml.includes('typingFieldContains(eventPoint.position)'));
assert(qml.includes('gesturePolicy: TapHandler.DragThreshold'));assert(!qml.includes('gesturePolicy: TapHandler.WithinBounds'));
assert(qml.includes('GoodBot.InkPad'));assert(/Layout\.bottomMargin:\s*root\.keyboardInset/.test(qml));
assert.equal(ctx.deliveryNotice('TLS connection failed',true),'Agent delivery is not connected yet. Submissions remain in Queued.');
assert.equal(ctx.deliveryNotice('Snapshot write failed',false),'Snapshot write failed');
console.log('Native keyboard opens from the typing fields and outside taps dismiss it without a toolbar button');

const retryState=m.fresh(),retryId=enqueueLegacy(retryState,'Exact retained message',4);
const retrySent=[],retryCtx={Model:m,state:retryState,loaded:true,saving:false,dirty:false,syncing:false,
    activeItemId:'',notice:'',send:(...args)=>retrySent.push(args)};
vm.createContext(retryCtx);vm.runInContext(qml.slice(qml.indexOf('    function outboxItem('),qml.indexOf('    function submit(')),retryCtx);
const retrySnapshot=JSON.stringify(retryState.outbox[0]);retryCtx.retryOutbox(retryId);
assert.deepEqual(retrySent,[[4,JSON.stringify(retryState.outbox[0].payload)]]);
assert.equal(JSON.stringify(retryState.outbox[0]),retrySnapshot);assert.equal(retryCtx.outboxText(retryState.outbox[0]),'Exact retained message');
console.log('Manual retry preserves legacy queued ID and payload exactly');

const oldDraft={capture:capture,comment:'keep',converted:'',destination:0,ink:''};
ctx.state=m.fresh();ctx.state.draft=oldDraft;ctx.capture=oldDraft.capture;
ctx.receive(2,JSON.stringify({source:{page_id:'new'},image:capture.image}));
assert.equal(ctx.capture.source.page_id,'page');assert.strictEqual(ctx.state.draft,oldDraft);
const draftCode=qml.slice(qml.indexOf('    function draft('),qml.indexOf('    function outboxItem('));
const draftCtx={capture:oldDraft.capture,restoring:false,state:{draft:{ink:'previous'}},messageText:'keep',converted:{text:''},destination:{currentIndex:0},
    draftSave:{restart:()=>assert.fail('must not replace failed drawing')},ink:{hasInk:true,dataUrl:()=>'',error:'PNG encoding failed'},unrestoredInk:'',notice:''};
vm.createContext(draftCtx);vm.runInContext(draftCode,draftCtx);
assert.equal(draftCtx.draft(),false);assert.equal(draftCtx.state.draft.ink,'previous');
console.log('New captures and failed drawing serialization preserve the existing draft');

const staged=m.fresh();staged.events=[
    {kind:'capture_accepted',request_id:'capture-1',destination:'obsidian',source:capture.source,original:{mode:'raw_image'},created_at:'2026-10-19T10:00:00Z'},
    {kind:'handoff_update',request_id:'capture-1',message_id:'capture-1',status:'handed_off'},
    {kind:'stage_update',request_id:'capture-1',stage:'raw_stored',state:'completed',evidence:{summary:'Original preserved'},created_at:'2026-10-19T10:01:00Z'},
    {kind:'agent_reply',message:{message_id:'not-a-submission',request_id:'capture-1',actor:'hermes',text:'private result'}}
];
accepted=m.historySubmissions(staged);
assert.equal(accepted.length,1);assert.equal(accepted[0].source.page_id,'page');
assert.equal(m.submissionStatus(accepted[0]),'Original stored (raw_stored) · complete');assert(accepted[0].summary.includes('Original image saved'));
console.log('Accepted capture history exposes source and exact processing stage without treating an agent reply as a submission');

{
const sessionQml=fs.readFileSync(__dirname+'/native/Session.qml','utf8');
let popupOpens=0,queueNotices=[],savedSnapshots=[];
const directClient={Model:m,state:m.fresh(),loaded:true,restoring:false,saving:false,dirty:false,syncing:false,
    vaultSavePending:false,closeAfterSave:false,wanted:false,activeItemId:'',notice:'',pane:'compose',capture:oldDraft.capture,
    messageText:'keep',sourcePreview:'',unrestoredInk:'',converted:{text:''},destination:{currentIndex:0},
    ink:{hasInk:false,clear:()=>{}},draftSave:{restart:()=>{}},queuedList:{model:[]},historyList:{model:[]},
    queuedCount:0,historyCount:0,selectedSubmissionId:'',selectedSubmission:null,selectedQueued:false,Qt:{callLater:f=>f()},
    nativeEndpoint:{encodeImage:()=>encoded},persist:()=>{directClient.saving=true;directClient.dirty=false;savedSnapshots.push(JSON.stringify(directClient.state));},
    setMessageText:v=>directClient.messageText=v,unsavedClose:{visible:false}};
directClient.state.draft=oldDraft;
Object.defineProperty(directClient,'hasActiveDraft',{get:()=>directClient.state.draft!==null||directClient.capture!==null});
const directSession={Model:m,capturingPage:false,vaultStatus:msg=>queueNotices.push(msg),panel:{client:directClient,showPopup:()=>popupOpens++},console:{log:()=>{}}};
directClient.GoodBot={Session:directSession};vm.createContext(directClient);vm.runInContext(receiveCode,directClient);vm.runInContext(draftCode,directClient);
vm.createContext(directSession);vm.runInContext(sessionQml.slice(sessionQml.indexOf('    function openInbox('),sessionQml.lastIndexOf('\n}')),directSession);
const rect={x:20,y:40,width:80,height:120},bounds={x:-20,y:0,width:400,height:800};
const preservedDraft=JSON.stringify(directClient.state.draft);
directSession.capture({},'book','page','Ideas','2',rect,bounds,'obsidian');
assert.equal(popupOpens,0);assert.strictEqual(directClient.capture,oldDraft.capture);assert.equal(JSON.stringify(directClient.state.draft),preservedDraft);
assert.equal(directClient.state.outbox.length,1);assert.equal(directClient.state.outbox[0].payload.destination,'obsidian');
assert.equal(JSON.parse(savedSnapshots[0]).draft.comment,'keep');assert.equal(queueNotices.length,0);
directClient.receive(1,'');assert.equal(queueNotices.at(-1),'Queued locally for vault');
assert.equal(directClient.outboxStatus(directClient.state.outbox[0]),'Queued for vault; awaiting relay receipt');
directSession.openSend({});assert.equal(popupOpens,1);assert.equal(directClient.pane,'compose');
directClient.capture=null;directClient.state.draft=null;directClient.pane='queued';
directSession.capture({},'book','page','Ideas','2',rect,bounds,'');
assert.equal(popupOpens,2);assert.equal(directClient.pane,'compose');assert.equal(directClient.capture.source.selection,'crop');
console.log('Direct vault queue bypasses composer and preserves the active capture draft; Send to agent still opens capture compose');
}

const metadataState=m.fresh(),submitted='2026-10-20T03:15:00Z',received='2026-10-20T03:16:00Z';
const metadataId=m.queue(metadataState,{source:{...capture.source,selection:'crop'},image:capture.image},'obsidian',null,'','');
metadataState.outbox[0].created_at=submitted;
assert(m.submissionTimes(metadataState.outbox[0],true).startsWith('Submitted: '+m.submissionTime(submitted)));
assert(!m.submissionTimes(metadataState.outbox[0],true).includes('Accepted:'));
m.accept(metadataState,{request_id:metadataId,status:'handed_off'});
const acceptedEvent={kind:'capture_accepted',request_id:metadataId,source:{...capture.source,selection:'crop'},created_at:received};
metadataState.events.push(acceptedEvent,{kind:'stage_update',request_id:metadataId,stage:'raw_stored',state:'completed',created_at:'2026-10-21T10:00:00Z'});
let metadata=m.historySubmissions(metadataState)[0];
assert.equal(metadata.submitted_at,submitted);assert.equal(metadata.accepted_at,received);
assert.equal(m.submissionSource(metadata.source),'Source: Ideas · Page 2 · Lasso selection');
const metadataText=m.submissionTimes(metadata,false);
assert.equal(metadataText,'Submitted: '+m.submissionTime(submitted)+'\nAccepted: '+m.submissionTime(received));
assert(/\(UTC[+-]\d{2}:\d{2}, device time\)/.test(metadataText));
metadataState.events.push(acceptedEvent,{kind:'handoff_update',request_id:metadataId,status:'handed_off',created_at:'2026-10-22T10:00:00Z'});
assert.equal(m.historySubmissions(metadataState).length,1);
assert.equal(m.submissionTimes(m.historySubmissions(metadataState)[0],false),metadataText);
const serverOnly=m.fresh();serverOnly.events=[acceptedEvent];
assert.equal(m.historySubmissions(serverOnly)[0].submitted_at,'');
assert.equal(m.historySubmissions(serverOnly)[0].accepted_at,received);
const stageOnly=m.fresh();stageOnly.events=[{kind:'stage_update',request_id:'old',created_at:received}];
assert.equal(m.submissionTimes(m.historySubmissions(stageOnly)[0],false),'Submitted: unavailable\nAccepted: unavailable');
const undated=m.fresh(),undatedId=enqueueLegacy(undated,'Old saved text');delete undated.outbox[0].created_at;
m.accept(undated,{request_id:undatedId,status:'handed_off'});
assert.equal(m.submissionTimes(m.historySubmissions(undated)[0],false),'Submitted: unavailable\nAccepted: unavailable');
assert.equal(m.submissionSource(null),'Source: unavailable');
assert.equal(m.submissionSource({selection:'page'}),'Source: Document title unavailable · Page unavailable · Whole page');
assert.equal(m.submissionTime('2026-10-20T03:15:00'),'unavailable');
assert.equal(m.submissionTime('not-a-dateZ'),'unavailable');
const local=new Date(submitted),formatted=m.submissionTime(submitted);
assert(formatted.includes(String(local.getHours()).padStart(2,'0')+':15'));
assert(qml.includes('Model.submissionSource((modelData.payload||{}).source)'));
assert(qml.includes('Model.submissionSource(modelData.source)'));
assert(qml.includes('Model.submissionTimes(selectedSubmission, selectedQueued)'));
console.log('Submission cards retain source, separate submit/accept times, explicit local offset and honest legacy gaps');
