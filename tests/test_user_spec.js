/* Simulated browser API tests. They do not prove an actual browser permission or save. */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const core = require('../assets/user-spec.js');

const hash = text => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
const hashFile = async file => crypto.createHash('sha256').update(Buffer.from(await file.arrayBuffer())).digest('hex');
const sample = () => ({schema:core.SCHEMA, document_id:'synthetic-id', version:'v001', status:'draft',
  confirmation:'unconfirmed', revision:1, title:'合成项目', summary:'', global_settings:'', layout_aspect:'', assets:[], shots:[]});
const row = () => ({id:'stable-id', number:'S001', duration_seconds:'1.9', thumbnail:'', content:'店员回头。',
  description:'@{店员} 回头看向门口。', shot_scale:'近景', camera_motion:'固定', prompt:'{镜头:近景}，{动态:角色看向门口}', video_prompt:'', mark:'',
  layout_camera:{type:'static', start_crop:[0, 0, 1, 1], end_crop:[0, 0, 1, 1]}});
const asset = () => ({id:'A01', name:'店员', path:'../05_参考资产/02_店员_idA01_v001_draft.png'});

test('malicious script terminator is serialized as data and Chinese/newlines round-trip', () => {
  const data = sample(); data.shots.push({...row(), prompt:'长中文\n</script><script>alert(1)</script>&\u2028' + '剧情。'.repeat(3000)});
  const encoded = core.encodeData(data);
  assert.ok(!encoded.includes('<') && !encoded.includes('>') && !encoded.includes('&'));
  assert.deepEqual(JSON.parse(encoded), data);
});

test('stable IDs, seconds and allowed local thumbnails are validated', () => {
  const data = sample(); data.shots = [row()];
  assert.equal(core.validate(data), null);
  for (const bad of ['-1','NaN','0','1e3','1.25','1.4166666666666667']) {
    const changed = structuredClone(data); changed.shots[0].duration_seconds = bad;
    assert.match(core.validate(changed), /时长/);
  }
  const duplicate = structuredClone(data); duplicate.shots.push({...row(), number:'S002'});
  assert.match(core.validate(duplicate), /标识/);
  assert.equal(core.safeThumbnail('../05_参考资产/01_角色/01_角色_v001_draft.png'), true);
  for (const p of ['../../secret.png','../05_参考资产/../private.png','https://example.com/x.png',
                   '../05_参考资产/%2e%2e/x.png','../05_参考资产/a.png?x=1']) assert.equal(core.safeThumbnail(p), false);
});

test('segment markup is hidden in plain text', () => {
  assert.equal(core.plainText('{镜头:近景}，{动态:角色看向门口}'), '近景，角色看向门口');
  assert.equal(core.plainText('{风格：暖光 @{A01}}，未标记{其他:x}'), '暖光 @{A01}，未标记{其他:x}');
});

test('v2 contract is exact: assets, marks and unknown fields', () => {
  const data = sample(); data.shots = [row()]; data.assets = [asset()];
  assert.equal(core.validate(data), null);
  const cases = [
    d => { d.shots[0].action = '旧字段'; },
    d => { delete d.shots[0].mark; },
    d => { d.shots[0].mark = 'urgent'; },
    d => { d.extra = 'x'; },
    d => { d.schema = 'kpl-user-spec-v1'; },
    d => { d.assets.push(asset()); },
    d => { d.assets[0].path = '../../secret.png'; },
    d => { d.assets[0].id = 'A 01'; },
    d => { d.assets[0].name = ' '; },
    d => { delete d.shots[0].video_prompt; },
    d => { delete d.shots[0].layout_camera; },
    d => { d.shots[0].layout_camera = '推近'; },
    d => { d.shots[0].layout_camera.end_crop = [0, 0, 0.5, 0.5]; },
    d => { d.shots[0].layout_camera = {type:'linear', start_crop:[0, 0, 1.2, 1], end_crop:[0, 0, 1, 1]}; },
    d => { d.shots[0].layout_camera = {type:'zoom', start_crop:[0, 0, 1, 1], end_crop:[0, 0, 1, 1]}; },
    d => { d.layout_aspect = '16x9'; },
    d => { d.layout_aspect = '0:9'; },
  ];
  for (const mutate of cases) {
    const changed = structuredClone(data); mutate(changed);
    assert.notEqual(core.validate(changed), null);
  }
  for (const mark of Object.keys(core.MARKS)) {
    const changed = structuredClone(data); changed.shots[0].mark = mark;
    assert.equal(core.validate(changed), null);
  }
});

test('wrong file, unlocked identity and unsaved-close checks', () => {
  const expected = {name:'01_项目规格_v001_draft.html', documentId:'abc', normalized:'same'};
  assert.equal(core.selectionError(expected, {...expected}), null);
  for (const change of [{name:'other.html'}, {documentId:'other'}, {normalized:'edited'}])
    assert.match(core.selectionError(expected, {...expected, ...change}), /不一致/);
  const data = sample(); assert.equal(core.canEdit(data), true);
  assert.equal(core.canEdit({...data, status:'final'}), false);
  assert.equal(core.canEdit({...data, confirmation:'confirmed'}), false);
  assert.equal(core.identityUnchanged(data, {...data, version:'v002'}), false);
  const guard = core.createSaveGuard();
  assert.equal(guard.begin(), true); assert.equal(guard.begin(), false);
  guard.end(); assert.equal(guard.begin(), true); guard.end();
  let prevented = false; const event = {preventDefault(){ prevented = true; }, returnValue:undefined};
  assert.equal(core.warnBeforeUnload(event, true), true); assert.equal(prevented, true);
  prevented = false; assert.equal(core.warnBeforeUnload(event, false), false); assert.equal(prevented, false);
});

function fakeHandle(initial, options = {}) {
  let disk = initial, pending = null, calls = 0, writes = 0, aborts = 0;
  return {
    async queryPermission(){ return options.permission || 'granted'; },
    async requestPermission(){ return options.request || 'denied'; },
    async getFile(){
      calls++;
      if (options.cancel) throw Object.assign(new Error('cancel'), {name:'AbortError'});
      if (options.changeOnRead === calls) disk = 'concurrent external change';
      const bytes = Buffer.from(options.badReadback && calls > 2 ? 'wrong readback' : disk);
      return {async text(){ return bytes.toString('utf8'); }, async arrayBuffer(){return Uint8Array.from(bytes).buffer;}};
    },
    async createWritable(){return {
      async write(value){writes++; if (options.writeFailure) throw new Error('write'); pending = value;},
      async close(){if (options.closeFailure) throw new Error('close'); disk = pending;},
      async abort(){aborts++; pending = null;}
    };},
    inspect(){return {disk,writes,aborts};}
  };
}

test('simulated selected-handle saves and repeated saves read back actual content', async () => {
  const handle = fakeHandle('original');
  const first = await core.saveToHandle(handle, hash('original'), 'first', hash, hashFile);
  assert.equal(first.status, 'saved'); assert.equal(handle.inspect().disk, 'first');
  const second = await core.saveToHandle(handle, first.hash, 'second', hash, hashFile);
  assert.equal(second.status, 'saved'); assert.equal(handle.inspect().disk, 'second');
});

test('simulated stale file and race before write are blocked', async () => {
  const old = fakeHandle('changed');
  assert.equal((await core.saveToHandle(old, hash('original'), 'new', hash, hashFile)).status, 'conflict');
  assert.equal(old.inspect().writes, 0);
  const race = fakeHandle('original', {changeOnRead:2});
  assert.equal((await core.saveToHandle(race, hash('original'), 'new', hash, hashFile)).status, 'conflict');
  assert.equal(race.inspect().writes, 0); assert.equal(race.inspect().aborts, 1);
  const byteChanged = fakeHandle('\ufefforiginal');
  assert.equal((await core.saveToHandle(byteChanged, hash('original'), 'new', hash, hashFile)).status, 'conflict');
  assert.equal(byteChanged.inspect().writes, 0);
});

test('simulated cancellation, permission denial, failures and readback mismatch do not claim success', async () => {
  const cases = [[{cancel:true}, 'cancelled'], [{permission:'prompt'}, 'permission_denied'],
    [{writeFailure:true}, 'failed'], [{closeFailure:true}, 'write_unconfirmed'], [{badReadback:true}, 'readback_mismatch']];
  for (const [options, expected] of cases)
    assert.equal((await core.saveToHandle(fakeHandle('original', options), hash('original'), 'new', hash, hashFile)).status, expected);
});

test('simulated clipboard failure selects text for manual copy', async () => {
  let focused=false, selected=false;
  const control={focus(){focused=true;},select(){selected=true;}};
  assert.equal(await core.copyPrompt('中文提示词', {async writeText(){throw new Error('denied');}}, control), 'manual');
  assert.ok(focused && selected);
  assert.equal(await core.copyPrompt('中文提示词', {async writeText(){}}, control), 'copied');
});

test('file-backed mock exercises real temporary disk bytes, not a browser', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'kpl-html-save-'));
  const file = path.join(dir, 'fictional.html');
  try {
    await fs.writeFile(file, 'before', 'utf8');
    const handle = {async queryPermission(){return 'granted';},
      async getFile(){const bytes=await fs.readFile(file);return {async text(){return bytes.toString('utf8');},async arrayBuffer(){return Uint8Array.from(bytes).buffer;}};},
      async createWritable(){let pending=''; return {async write(x){pending=x;}, async close(){await fs.writeFile(file,pending,'utf8');}, async abort(){}};}};
    assert.equal((await core.saveToHandle(handle, hash('before'), '合成测试', hash, hashFile)).status, 'saved');
    assert.equal(await fs.readFile(file,'utf8'), '合成测试');
  } finally { await fs.unlink(file).catch(() => {}); await fs.rmdir(dir); }
});
