const assert=require('assert'),fs=require('fs'),vm=require('vm');const m={};vm.createContext(m);vm.runInContext(fs.readFileSync(__dirname+'/native/NotebookControl.js','utf8'),m);
const entries={};const root={id:'root',parentId:'',visibleName:'My files',type:0,isTrashed:false,childIds:[]};entries.root=root;
function doc(id,label){const d={id,parentId:'root',visibleName:label,type:1,isTrashed:false,isImported:false,templates:['Blank'],pages:['page-1'],tags:[],pageTagRecords:[],isNotepage:()=>true,templateForPage:i=>d.templates[i],idForPage:i=>d.pages[i],pageForId:p=>d.pages.indexOf(p),getPageTags:()=>d.pageTagRecords};Object.defineProperty(d,'pageCount',{get:()=>d.pages.length});entries[id]=d;root.childIds.push(id);return d;}
const c={library:{entryForId:id=>entries[id]||null,parentIdForId:id=>entries[id]?.parentId},documentType:1,rootId:()=> 'root',templates:()=>[{filename:'Blank',name:'Blank'},{filename:'Lined',name:'Lined'}],paperSize:{width:10,height:20},vertical:0,horizontal:1,view:null,lc:{},dc:{}};
c.lc.createDocument=(parent,name,id,page)=>{const d=doc(id,name);d.pages=[page];return id;};c.lc.setOrientation=()=>true;c.lc.setCoverPageNumber=()=>true;c.lc.setVisibleName=(id,name)=>{entries[id].visibleName=name;return true;};c.dc.setTemplateForPage=(id,index,t)=>{entries[id].templates[index]=t;return true;};c.dc.addPageWithTemplateAndPageSize=(id,index,t,size,callback,page)=>{let d=entries[id];if(index===-1)index=d.pageCount;d.pages.splice(index,0,page||'new-page');d.templates.splice(index,0,t);};
c.lc.setEntryTags=(id,selected,partial)=>{assert.deepEqual(Array.from(partial),[]);entries[id].tags=Array.from(selected);};
c.lc.setPageTags=(id,pages,selected,partial)=>{assert.deepEqual(Array.from(partial),[]);const d=entries[id],indexes=Array.from(pages);d.pageTagRecords=d.pageTagRecords.filter(t=>!indexes.includes(t.page));for(const page of indexes)for(const name of selected)d.pageTagRecords.push({page,name});};
const created=m.prepare(c,'create_notebook',{parent_id:'root',name:'Disposable',document_id:'doc-1',page_id:'page-1'}).poll();assert.equal(created.id,'doc-1');assert.deepEqual(Array.from(created.pages),['page-1']);
// Expected metadata is order independent; stale source identity blocks a mutation.
const before=JSON.parse(JSON.stringify(created)),reordered=Object.fromEntries(Object.entries(before).reverse());const added=m.prepare(c,'append_page',{id:'doc-1',expected:reordered,template:'Lined',page_id:'page-2'}).poll();assert.deepEqual(Array.from(added.pages),['page-1','page-2']);assert.throws(()=>m.prepare(c,'rename',{id:'doc-1',expected:before,name:'Changed'}),/changed/);
assert.throws(()=>m.prepare(c,'append_page',{id:'doc-1',expected:added,template:'Invented'}),/installed/);assert.throws(()=>m.prepare(c,'delete_pages',{id:'doc-1',expected:added,page_ids:['page-1']}),/confirmation/);assert.throws(()=>m.prepare(c,'rename',{id:'doc-1',expected:added,name:'../bad'}),/name/);
c.lc.moveEntriesToTrash=ids=>{entries[ids[0]].isTrashed=true;return true;};c.lc.restoreEntriesFromTrash=ids=>{entries[ids[0]].isTrashed=false;return true;};const trashed=m.prepare(c,'trash',{id:'doc-1',expected:added}).poll();assert.equal(trashed.trashed,true);const restored=m.prepare(c,'restore',{id:'doc-1',expected:trashed}).poll();assert.equal(restored.trashed,false);
c.dc.deletePages=(id,indexes)=>{indexes.slice().sort((a,b)=>b-a).forEach(i=>{entries[id].pages.splice(i,1);entries[id].templates.splice(i,1);});return true;};const deleted=m.prepare(c,'delete_pages',{id:'doc-1',expected:restored,page_ids:['page-2'],confirmed:true,backup_sha256:'a'.repeat(64)}).poll();assert.deepEqual(Array.from(deleted.pages),['page-1']);
assert.throws(()=>m.prepare(c,'delete_pages',{id:'doc-1',expected:deleted,page_ids:['page-1'],confirmed:true,backup_sha256:'a'.repeat(64)}),/every page/);
console.log('Native create/append/template allowlist, stale-state guards, Trash/restore and bounded backed-up page deletion passed');
// Document and page tags remain separate; add/remove preserve every unmentioned tag.
let tagState=m.prepare(c,'list_tags',{id:'doc-1'}).immediate;
tagState=m.prepare(c,'add_tags',{id:'doc-1',expected:tagState,tags:['active','interview']}).poll();
assert.deepEqual(Array.from(tagState.document_tags),['active','interview']);assert.deepEqual(JSON.parse(JSON.stringify(tagState.page_tags)),{});
c.dc.addPageWithTemplateAndPageSize('doc-1',-1,'Blank',c.paperSize,()=>{},'page-3');tagState=m.prepare(c,'list_tags',{id:'doc-1'}).immediate;
c.lc.setPageTags('doc-1',[1],['idea'],[]);tagState=m.prepare(c,'list_tags',{id:'doc-1'}).immediate;
tagState=m.prepare(c,'add_tags',{id:'doc-1',expected:tagState,page_id:'page-1',tags:['follow-up','review']}).poll();
assert.deepEqual(Array.from(tagState.document_tags),['active','interview']);assert.deepEqual(Array.from(tagState.page_tags['page-1']),['follow-up','review']);assert.deepEqual(Array.from(tagState.page_tags['page-3']),['idea']);
const listed=m.prepare(c,'list_tags',{id:'doc-1'}).immediate;assert.deepEqual(Array.from(listed.document_tags),['active','interview']);assert.deepEqual(Array.from(listed.page_tags['page-1']),['follow-up','review']);
tagState=m.prepare(c,'remove_tags',{id:'doc-1',expected:m.prepare(c,'list_tags',{id:'doc-1'}).immediate,tags:['active']}).poll();assert.deepEqual(Array.from(tagState.document_tags),['interview']);
tagState=m.prepare(c,'remove_tags',{id:'doc-1',expected:m.prepare(c,'list_tags',{id:'doc-1'}).immediate,page_id:'page-1',tags:['review']}).poll();assert.deepEqual(Array.from(tagState.page_tags['page-1']),['follow-up']);assert.deepEqual(Array.from(tagState.page_tags['page-3']),['idea']);
assert.throws(()=>m.prepare(c,'add_tags',{id:'doc-1',expected:m.prepare(c,'list_tags',{id:'doc-1'}).immediate,tags:['bad/name']}),/name/);assert.throws(()=>m.prepare(c,'add_tags',{id:'doc-1',expected:m.prepare(c,'list_tags',{id:'doc-1'}).immediate,tags:['idea','idea']}),/Duplicate/);
tagState=m.prepare(c,'add_tags',{id:'doc-1',expected:m.prepare(c,'list_tags',{id:'doc-1'}).immediate,tags:['constructor','toString']}).poll();assert.deepEqual(Array.from(tagState.document_tags),['constructor','interview','toString']);
console.log('Native document/page tag list, add and remove preserve unrelated tags and reject unsafe input');
// Native-copy requests must leave source identity/order intact and discover one new document.
c.lc.cloneEntry=(id,parent)=>{const source=entries[id],copy=doc('copied-doc','Disposable copy');copy.pages=source.pages.slice();copy.templates=source.templates.slice();copy.tags=source.tags.slice();copy.pageTagRecords=source.pageTagRecords.map(tag=>({...tag}));return copy.id;};
const copyState=m.prepare(c,'inspect',{id:'doc-1'}).immediate;const copied=m.prepare(c,'copy_item',{id:'doc-1',expected:copyState,parent_id:'root'}).poll();assert.equal(copied.id,'copied-doc');assert.deepEqual(Array.from(entries['doc-1'].pages),['page-1','page-3']);

// Execute the real persisted-state verifier against an original-format imported PDF.
const qml=fs.readFileSync(__dirname+'/native/NotebookControl.qml','utf8');const verify={pending:{operation:'trash'},Logic:{canonical:m.canonical},endpoint:{savedState:()=>({metadata:{visibleName:'PDF',parent:'trash'},content:{pageCount:1,pages:['original-page']},metadata_sha256:'x'})}};
vm.createContext(verify);vm.runInContext(qml.slice(qml.indexOf('    function savedItem('),qml.indexOf('    function check()')),verify);
assert.equal(verify.savedItem({id:'pdf',name:'PDF',trashed:true,pages:['original-page'],templates:['Blank']}),true);
assert.equal(verify.savedItem({id:'pdf',name:'PDF',trashed:true,pages:['wrong-page']}),false);
verify.pending.operation='append_page';assert.equal(verify.savedItem({id:'pdf',name:'PDF',trashed:true,pages:['original-page']}),false);
// Tag mutations are complete only after exact saved content tags and stable page IDs match.
verify.pending.operation='add_tags';verify.endpoint.savedState=()=>({metadata:{visibleName:'Tagged',parent:'root'},content:{pageCount:1,pages:['page-1'],tags:[{name:'interview',timestamp:2},{name:'active',timestamp:1}],pageTags:[{name:'review',pageId:'page-1',timestamp:3}]},metadata_sha256:'m',content_sha256:'c'});
const taggedItem={id:'tagged',name:'Tagged',parent_id:'root',trashed:false,pages:['page-1'],templates:['Blank'],document_tags:['active','interview'],page_tags:{'page-1':['review']}};
assert.equal(verify.savedItem(taggedItem),true);verify.endpoint.savedState=()=>({metadata:{visibleName:'Tagged',parent:'root'},content:{pageCount:1,pages:['page-1'],tags:[{name:'interview'}],pageTags:[{name:'review',pageId:'page-1'}]}});assert.equal(verify.savedItem(taggedItem),false);
console.log('Persisted tag proof requires exact document and page tags from saved content');
