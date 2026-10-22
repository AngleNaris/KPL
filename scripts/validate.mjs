import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const rel = p => path.relative(root,p).replaceAll('\\','/');
const read = p => fs.readFileSync(path.join(root,p),'utf8');
const digest = p => crypto.createHash('sha256').update(fs.readFileSync(path.join(root,p))).digest('hex');
const walk = dir => fs.readdirSync(dir,{withFileTypes:true}).flatMap(e =>
  e.name === '.git' || e.name === 'node_modules' ? [] :
    e.isDirectory() ? walk(path.join(dir,e.name)) : [path.join(dir,e.name)]);
const checks=[];
const check = (name,fn) => {fn(); checks.push(name);};

const files=walk(root);
check('single entry and required modules',()=>{
  assert.deepEqual(files.filter(p=>path.basename(p)==='SKILL.md').map(rel),['SKILL.md']);
  for(const p of ['README.md','NOTICE','LICENSE','references/directing.md','references/video.md','references/image.md','docs/sources.json','tests/behavior.md']) assert(fs.statSync(path.join(root,p)).isFile(),p);
  assert.deepEqual(fs.readdirSync(path.join(root,'references/formats')).sort(),['gpt-image-2.5.md','minimax-h3.md','seedance-2.5.md']);
});
check('frontmatter (supported flat YAML subset)',()=>{
  const m=read('SKILL.md').match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/);assert(m,'frontmatter');
  const data=Object.fromEntries(m[1].split(/\r?\n/).map(line=>{
    const v=line.match(/^(name|description): (.+)$/); assert(v,'unexpected field or unsupported YAML shape'); return [v[1],v[2]];
  }));
  assert.equal(data.name,'kpl');assert(data.description.length>0&&data.description.length<=1024);
  assert(!/[<>]/.test(data.description));assert(!/\[TODO:/.test(read('SKILL.md')));
});
const urls=new Set(); let internalLinks=0;
check('UTF-8, relative link containment, citation targets, fenced JSON',()=>{
  for(const file of files.filter(p=>p.endsWith('.md'))){
    const b=fs.readFileSync(file);const s=b.toString('utf8');assert(Buffer.from(s,'utf8').equals(b),`invalid UTF-8: ${rel(file)}`);assert(!s.includes('\uFFFD'),`replacement character: ${rel(file)}`);
    for(const m of s.matchAll(/\[[^\]]*\]\(([^\s)]+)\)/g)){
      const target=m[1]; if(/^https:\/\//.test(target)){urls.add(target);continue;}
      assert(!/^[a-z]+:/i.test(target),`nonportable protocol: ${target}`);
      const [p,anchor]=target.split('#');const abs=path.resolve(path.dirname(file),decodeURIComponent(p||'.'));
      assert(abs===root||abs.startsWith(root+path.sep),`link escapes repository: ${target}`);
      assert(fs.existsSync(abs),`missing target: ${rel(file)} -> ${target}`);internalLinks++;
      if(anchor){const headings=fs.readFileSync(abs,'utf8').split(/\r?\n/).filter(l=>/^#+ /.test(l));assert(headings.length,`no heading for anchor: ${target}`);}
    }
    for(const m of s.matchAll(/```json\r?\n([\s\S]*?)\r?\n```/g))JSON.parse(m[1]);
  }
});
const sources=JSON.parse(read('docs/sources.json'));
check('pinned sources and complete license bytes',()=>{
  assert.equal(sources.checkedOn,'2026-10-22');assert.equal(sources.rawLocalInstructionsRedistributed,false);
  for(const x of sources.records){assert(/^[0-9a-f]{40}$/.test(x.commit));assert(x.readFiles.every(f=>/^[0-9a-f]{64}$/.test(f.sha256)));}
  for(const x of sources.licenses)assert.equal(digest(x.path),x.sha256,x.path);
  const upstreamLicense=id=>sources.records.find(x=>x.id===id).readFiles.find(x=>x.path==='LICENSE').sha256;
  assert.equal(digest('LICENSES/video-prompting-Apache-2.0.txt'),upstreamLicense('video-prompting'));
  assert.equal(digest('LICENSES/DirectorSKILL-MIT.txt'),upstreamLicense('DirectorSKILL'));
  assert.equal(digest('LICENSES/imagegen-Apache-2.0.txt'),sources.local.find(x=>x.id==='imagegen').readFiles.find(x=>x.path==='LICENSE.txt').sha256);
  assert(read('LICENSES/DirectorSKILL-MIT.txt').includes('Copyright (c) 2026 wangzhang-wu'));
  assert(read('LICENSES/video-prompting-Apache-2.0.txt').includes('END OF TERMS AND CONDITIONS'));
  assert(read('LICENSES/imagegen-Apache-2.0.txt').includes('END OF TERMS AND CONDITIONS'));
});
const baseFields=['integrated_multimodal_description','overall_soundscape','non_diegetic_music'];
const refFields=['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'];
function validateH3(s,expected){
  assert.deepEqual([...s.matchAll(/^([a-z_]+):/gm)].map(m=>m[1]),expected,'H3 field order/duplicates');
  assert(s.includes('[Shot 1]'));assert(!/\[Shot 1\]\s*At \d/.test(s),'first shot must not have cut timestamp');
  if(expected===refFields){
    assert(/summary:\s*\[(?:[a-z ]+)(?: \+ [a-z ]+)*\]/.test(s));
    assert(s.includes('attribute_transfer -'));
  }
}
check('saved behavior output inventory and H3 syntax (not semantic grading)',()=>{
  for(let i=1;i<=10;i++)assert(read(`tests/outputs/B${String(i).padStart(2,'0')}.md`).trim().length>60);
  validateH3(read('tests/outputs/B04.md'),baseFields);
  validateH3(read('tests/outputs/B09.md'),baseFields);
  validateH3(read('tests/outputs/B10.md'),refFields);
  assert(read('tests/outputs/B09.md').startsWith('How the reference pictures align with the target video'));
  assert(read('tests/outputs/B09.md').includes('5.00-second mark'));
});
check('format validator rejects invalid field order, duplicates and first-shot time',()=>{
  const s=read('tests/outputs/B04.md');
  assert.throws(()=>validateH3(s.replace('overall_soundscape:','non_diegetic_music:'),baseFields));
  assert.throws(()=>validateH3(s+'\noverall_soundscape: duplicate',baseFields));
  assert.throws(()=>validateH3(s.replace('[Shot 1]','[Shot 1] At 00:00.000,'),baseFields));
});
check('publication hygiene',()=>{
  const prose=files.filter(p=>/\.(md|json)$/.test(p)&&!p.endsWith('link-results.json')).map(p=>read(rel(p))).join('\n');
  assert(!/[A-Z]:[\\/](?:Users|Program Files)/.test(prose),'machine path');
  assert(!/gh[pousr]_[A-Za-z0-9]{20,}/.test(prose),'credential-like string');
  assert(!/sk-[A-Za-z0-9_-]{30,}/.test(prose),'credential-like string');
  assert(!files.some(p=>/(?:config\.json|\.env|\.pem)$/i.test(p)),'private configuration');
});
console.log(JSON.stringify({status:'PASS',checks:checks.length,files:files.length,internalLinks,externalUrls:urls.size,details:checks},null,2));

if(process.argv.includes('--links')){
  const queue=[...urls].sort();const results=[];
  async function worker(){
    while(queue.length){const url=queue.shift();try{
      const r=await fetch(url,{redirect:'follow',signal:AbortSignal.timeout(12000),headers:{'User-Agent':'KPL-link-validator'}});
      results.push({url,status:r.status,ok:r.ok,finalUrl:r.url});await r.body?.cancel();
    }catch(e){results.push({url,ok:false,error:e.name+': '+e.message});}}
  }
  await Promise.all(Array.from({length:4},worker));results.sort((a,b)=>a.url.localeCompare(b.url));
  const report={checkedAt:new Date().toISOString(),meaning:'Reachability only, not format/content/generation validation',results};
  fs.writeFileSync(path.join(root,'tests/link-results.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({links:results.length,reachable:results.filter(r=>r.ok).length,unresolved:results.filter(r=>!r.ok)},null,2));
  if(results.some(r=>!r.ok))process.exitCode=1;
}
