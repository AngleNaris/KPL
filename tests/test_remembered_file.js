/* Simulated IndexedDB and file handles: this is not a real browser test. */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {test} = require('node:test');
const core = require('../assets/user-spec.js');

const hash = value => crypto.createHash('sha256').update(value, 'utf8').digest('hex');
const hashFile = async file => crypto.createHash('sha256').update(Buffer.from(await file.arrayBuffer())).digest('hex');

class FakeIndexedDB {
  constructor() {this.map = new Map(); this.created = false; this.failOpen = false; this.failPut = false;}
  open() {
    const request = {};
    queueMicrotask(() => {
      if (this.failOpen) {request.error = new Error('storage denied'); request.onerror(); return;}
      const db = {objectStoreNames:{contains:()=>this.created}, createObjectStore:()=>{this.created=true;},
                  close(){}, transaction:()=>this.transaction()};
      request.result = db;
      if (!this.created && request.onupgradeneeded) request.onupgradeneeded();
      if (request.onsuccess) request.onsuccess();
    });
    return request;
  }
  transaction() {
    let pending = 0, aborted = false;
    const tx = {error:null, abort(){aborted=true; queueMicrotask(()=>tx.onabort && tx.onabort());}};
    const queue = fn => {
      const req = {}; pending++;
      queueMicrotask(() => {
        if (aborted) return;
        try {req.result = fn(); if (req.onsuccess) req.onsuccess();}
        catch (error) {tx.error=error; aborted=true; if (tx.onerror) tx.onerror(); return;}
        finally {pending--;}
        queueMicrotask(() => {if (!aborted && pending === 0 && tx.oncomplete) tx.oncomplete();});
      });
      return req;
    };
    tx.objectStore = () => ({
      get:key=>queue(()=>this.map.get(key)),
      put:record=>queue(()=>{if(this.failPut) throw new Error('clone failed'); this.map.set(record.key,record); return record.key;}),
      delete:key=>queue(()=>this.map.delete(key))
    });
    return tx;
  }
}

function fakeFileHandle(initial, options={}) {
  let disk=initial, writes=0, requests=0, queries=0;
  return {
    kind:'file', name:options.name || '01_项目规格_v001_draft.html',
    async queryPermission(){queries++; if(options.queryError) throw new Error('query'); return options.permission || 'granted';},
    async requestPermission(){requests++; if(options.cancel) throw Object.assign(new Error('cancel'),{name:'AbortError'}); return options.request || 'denied';},
    async getFile(){
      if(options.missing) throw Object.assign(new Error('moved'),{name:'NotFoundError'});
      const bytes=Buffer.from(disk,'utf8');
      return {name:this.name,async text(){return bytes.toString('utf8');},async arrayBuffer(){return Uint8Array.from(bytes).buffer;}};
    },
    async createWritable(){let pending; return {async write(s){writes++;pending=s;},async close(){disk=pending;},async abort(){}};},
    change(value){disk=value;}, inspect(){return {disk,writes,requests,queries};}
  };
}

const url = 'file:///C:/synthetic/project-a/01_项目说明/01_项目规格_v001_draft.html';
const doc = 'fictional-id';
const source = JSON.stringify({id:doc, content:'初始'});
const parse = text => ({documentId:JSON.parse(text).id, normalized:text});
const expected = text => ({name:'01_项目规格_v001_draft.html', documentId:doc, normalized:text});

test('association key isolates page path and stable document ID', () => {
  const a=core.associationKey(doc,url);
  assert.notEqual(a,core.associationKey(doc,url.replace('project-a','project-b')));
  assert.notEqual(a,core.associationKey('different-id',url));
  assert.equal(a,core.associationKey(doc,url+'?ignored=1#section'));
  assert.equal(core.associationKey(doc,'https://example.com/spec.html'),null);
});

test('remember only after explicit action; simulated reload restores and updates hash', async () => {
  const db=new FakeIndexedDB(), store=core.createHandleStore(db), key=core.associationKey(doc,url);
  const handle=fakeFileHandle(source);
  assert.equal(await store.get(key),undefined);
  assert.equal(handle.inspect().requests,0);
  const saved=await core.rememberAssociation(store,key,doc,'association-a',handle,handle.name,hash(source),hashFile);
  assert.equal(saved.status,'remembered');
  assert.equal(handle.inspect().requests,0);
  assert.deepEqual(Object.keys(await store.get(key)).sort(),
                   ['association_id','document_id','handle','key','last_sha256','schema']);
  const reopenedStore=core.createHandleStore(db), record=await reopenedStore.get(key);
  const notConfirmed=await core.restoreAssociation(reopenedStore,key,record,expected(source),parse,hashFile,false);
  assert.equal(notConfirmed.status,'needs_confirmation');
  const restored=await core.restoreAssociation(reopenedStore,key,record,expected(source),parse,hashFile,true);
  assert.equal(restored.status,'ready');
  const next=JSON.stringify({id:doc,content:'已编辑'});
  const written=await core.saveToHandle(restored.handle,restored.hash,next,hash,hashFile);
  assert.equal(written.status,'saved');
  assert.equal(await reopenedStore.updateHash(key,record.association_id,hash(source),written.hash),true);
  const afterReload=await reopenedStore.get(key);
  assert.equal((await core.restoreAssociation(reopenedStore,key,afterReload,expected(next),parse,hashFile,true)).status,'ready');
});

test('permission must be requested after confirmation; denial and cancellation never write', async () => {
  for (const [options, outcome] of [[{permission:'prompt',request:'denied'},'permission_denied'],
                                    [{permission:'prompt',cancel:true},'cancelled']]) {
    const db=new FakeIndexedDB(), store=core.createHandleStore(db), key=core.associationKey(doc,url);
    const handle=fakeFileHandle(source,options);
    const saved=await core.rememberAssociation(store,key,doc,'a',handle,handle.name,hash(source),hashFile);
    const result=await core.restoreAssociation(store,key,saved.record,expected(source),parse,hashFile,true);
    assert.equal(result.status,outcome); assert.equal(handle.inspect().requests,1); assert.equal(handle.inspect().writes,0);
  }
  const db=new FakeIndexedDB(), store=core.createHandleStore(db), key=core.associationKey(doc,url);
  const handle=fakeFileHandle(source,{permission:'prompt',request:'granted'});
  const saved=await core.rememberAssociation(store,key,doc,'a',handle,handle.name,hash(source),hashFile);
  assert.equal((await core.restoreAssociation(store,key,saved.record,expected(source),parse,hashFile,true)).status,'ready');
  assert.equal(handle.inspect().requests,1);
});

test('moved, wrong file, changed bytes, removed association and replaced handle are blocked', async () => {
  const db=new FakeIndexedDB(), store=core.createHandleStore(db), key=core.associationKey(doc,url);
  const handle=fakeFileHandle(source), remembered=await core.rememberAssociation(store,key,doc,'a',handle,handle.name,hash(source),hashFile);
  const wrongExpected={...expected(source),name:'other.html'};
  assert.equal((await core.restoreAssociation(store,key,remembered.record,wrongExpected,parse,hashFile,true)).status,'mismatch');
  handle.change(JSON.stringify({id:doc,content:'external change'}));
  assert.equal((await core.restoreAssociation(store,key,remembered.record,expected(source),parse,hashFile,true)).status,'mismatch');
  handle.change(source+' ');
  const ignoresWhitespace=text=>({documentId:JSON.parse(text).id, normalized:source});
  assert.equal((await core.restoreAssociation(store,key,remembered.record,expected(source),ignoresWhitespace,hashFile,true)).status,'conflict');
  handle.change(source);
  assert.equal(await store.forget(key,'a'),true);
  assert.equal((await core.restoreAssociation(store,key,remembered.record,expected(source),parse,hashFile,true)).status,'association_changed');
  const moved=fakeFileHandle(source,{missing:true});
  const movedRecord={...remembered.record,handle:moved}; await store.put(movedRecord);
  assert.equal((await core.restoreAssociation(store,key,movedRecord,expected(source),parse,hashFile,true)).status,'file_unavailable');
  await store.put({...movedRecord,association_id:'replacement'});
  assert.equal((await core.restoreAssociation(store,key,movedRecord,expected(source),parse,hashFile,true)).status,'association_changed');
  assert.equal(handle.inspect().writes,0);
});

test('atomic association update and forget do not overwrite a newer record', async () => {
  const db=new FakeIndexedDB(), store=core.createHandleStore(db), key=core.associationKey(doc,url);
  const handle=fakeFileHandle(source), first=await core.rememberAssociation(store,key,doc,'a',handle,handle.name,hash(source),hashFile);
  const second=await core.rememberAssociation(store,key,doc,'b',handle,handle.name,hash(source),hashFile);
  assert.equal(await store.updateHash(key,'a',hash(source),hash('next')),false);
  assert.equal(await store.forget(key,'a'),false);
  assert.equal((await store.get(key)).association_id,second.record.association_id);
  assert.equal(await store.updateHash(key,'b',hash(source),hash('next')),true);
  assert.equal((await store.get(key)).last_sha256,hash('next'));
  assert.equal(await store.forget(key,'b'),true);
  assert.equal(await store.get(key),undefined);
  assert.equal(first.status,'remembered');
});

test('unavailable storage and stale selection never claim remembered', async () => {
  const key=core.associationKey(doc,url), handle=fakeFileHandle(source);
  const missingStore=core.createHandleStore(null);
  assert.equal((await core.rememberAssociation(missingStore,key,doc,'a',handle,handle.name,hash(source),hashFile)).status,'storage_failed');
  const db=new FakeIndexedDB(), store=core.createHandleStore(db);
  handle.change('changed');
  assert.equal((await core.rememberAssociation(store,key,doc,'a',handle,handle.name,hash(source),hashFile)).status,'conflict');
  assert.equal(await store.get(key),undefined);
  db.failPut=true; handle.change(source);
  assert.equal((await core.rememberAssociation(store,key,doc,'a',handle,handle.name,hash(source),hashFile)).status,'storage_failed');
});
