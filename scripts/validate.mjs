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
const historicalSkillChanges=[];
const check = (name,fn) => {fn(); checks.push(name);};

const files=walk(root);
check('single entry and required modules',()=>{
  assert.deepEqual(files.filter(p=>path.basename(p)==='SKILL.md').map(rel),['SKILL.md']);
  for(const p of ['README.md','NOTICE','LICENSE','references/spec-workflow.md','references/creative-review.md','assets/creative-spec.md','references/directing.md','references/video.md','references/image.md','docs/sources.json','tests/behavior.md','tests/spec-execution.json','tests/spec-review.json','tests/targeted-execution.json','tests/targeted-review.json']) assert(fs.statSync(path.join(root,p)).isFile(),p);
  assert.deepEqual(fs.readdirSync(path.join(root,'references/formats')).sort(),['gpt-image-2.5.md','minimax-h3.md','seedance-2.5.md']);
});
check('frontmatter (supported flat YAML subset)',()=>{
  const m=read('SKILL.md').match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/);assert(m,'frontmatter');
  const fields=m[1].split(/\r?\n/).map(line=>{
    const v=line.match(/^(name|description): (.+)$/); assert(v,'unexpected field or unsupported YAML shape'); return [v[1],v[2]];
  });
  assert.equal(new Set(fields.map(x=>x[0])).size,fields.length,'duplicate frontmatter key');
  const data=Object.fromEntries(fields);
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
      if(anchor){const headings=fs.readFileSync(abs,'utf8').split(/\r?\n/).filter(l=>/^#+ /.test(l));const slugs=headings.map(l=>l.replace(/^#+ /,'').trim().toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu,'').replace(/\s/g,'-'));assert(slugs.includes(decodeURIComponent(anchor)),`missing heading anchor: ${target}`);}
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
    assert(/\battribute_transfer\b/.test(s),'expected style-transfer relationship token');
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
  const ref=read('tests/outputs/B10.md');
  assert.throws(()=>validateH3(ref.replaceAll('attribute_transfer','unknown_relationship'),refFields));
});
check('creative-spec template and independent scenario schema',()=>{
  const template=read('assets/creative-spec.md');
  for(const h of ['项目与当前交付','风格与资产','镜头与模式','验收与未决问题','本轮变更影响'])assert(template.includes('## '+h),h);
  const suite=JSON.parse(read('tests/spec-inputs.json'));
  const rubric=JSON.parse(read('tests/spec-rubric.json'));
  assert.equal(suite.cases.length,9);assert.equal(rubric.version,1);
  const ids=suite.cases.map(x=>x.id);assert.equal(new Set(ids).size,ids.length);
  assert.deepEqual(ids,Array.from({length:9},(_,i)=>`S${String(i+1).padStart(2,'0')}`));
  assert.deepEqual(rubric.cases.map(x=>x.id),ids);
  for(const c of suite.cases)assert(typeof c.request==='string'&&c.request.length>30);
  for(const c of rubric.cases)assert(c.criteria.length>=3&&c.criteria.every(x=>typeof x==='string'&&x.length>10));
});
check('independent evidence consistency (not automated semantic grading)',()=>{
    const review=JSON.parse(read('tests/spec-review.json'));
    const rubric=JSON.parse(read('tests/spec-rubric.json'));
    const execution=JSON.parse(read('tests/spec-execution.json'));
    assert.equal(execution.actualGeneration,false);assert.equal(execution.rawResponsesEdited,false);
    assert.equal(execution.executions.length,2);
    for(const x of execution.skillFiles){
      assert(/^[0-9a-f]{64}$/.test(x.sha256),'historical skill digest: '+x.path);
      if(digest(x.path)!==x.sha256)historicalSkillChanges.push(x.path);
    }
    assert.deepEqual(execution.executions.flatMap(x=>x.cases),rubric.cases.map(x=>x.id));
    for(const run of execution.executions){
      assert(run.readFiles.includes('SKILL.md'));
      assert(!run.readFiles.includes('tests/spec-rubric.json'));
      for(const x of run.outputs)assert.equal(digest(x.path),x.sha256,'raw execution response changed: '+x.id);
    }
    assert.equal(review.method,'independent-text-review');
    assert.equal(review.actualGeneration,false);
    assert.deepEqual(review.cases.map(x=>x.id),rubric.cases.map(x=>x.id));
    for(const c of review.cases){
      const r=rubric.cases.find(x=>x.id===c.id);
      assert.equal(c.criteria.length,r.criteria.length,c.id);
      assert.equal(c.outputSha256,digest(c.output),c.id+' raw response hash');
      for(const judgement of c.criteria){assert(typeof judgement.pass==='boolean');assert(judgement.evidence.length>10);}
      assert.equal(c.pass,c.criteria.every(x=>x.pass),c.id+' summary');
    }
    assert.equal(review.passed,review.cases.filter(x=>x.pass).length);
    assert.equal(review.passed,rubric.cases.length,'independent review reports failures');
});
check('publication hygiene',()=>{
  const prose=files.filter(p=>/\.(md|json)$/.test(p)&&!p.endsWith('link-results.json')).map(p=>read(rel(p))).join('\n');
  assert(!/\b[A-Z]:[\\/]/i.test(prose),'machine path');
  assert(!/\b(?:rollout-\d{4}-\d{2}-\d{2}T|source_thread_id|transcript_evidence)\b/.test(prose),'private conversation metadata');
  assert(!/gh[pousr]_[A-Za-z0-9]{20,}/.test(prose),'credential-like string');
  assert(!/sk-[A-Za-z0-9_-]{30,}/.test(prose),'credential-like string');
  assert(!files.some(p=>/(?:config\.json|\.env|\.pem)$/i.test(p)),'private configuration');
  const readme=read('README.md');
  assert.deepEqual(readme.match(/^## .+$/gm),['## 结构'],'README stays focused on purpose and structure');
  assert(readme.trim().endsWith('https://github.com/wuwangzhang1216/DirectorSKILL)。'),'README source ending');
});
check('targeted text execution and independent review integrity (not semantic scoring)',()=>{
  const suite=JSON.parse(read('tests/targeted-inputs.json'));
  const rubric=JSON.parse(read('tests/targeted-rubric.json'));
  const execution=JSON.parse(read('tests/targeted-execution.json'));
  const review=JSON.parse(read('tests/targeted-review.json'));
  const ids=Array.from({length:8},(_,i)=>`R${String(i+1).padStart(2,'0')}`);
  assert.equal(suite.fictional,true);
  assert.deepEqual(suite.cases.map(c=>c.id),ids);
  assert.deepEqual(rubric.cases.map(c=>c.id),ids);
  assert(suite.cases.every(c=>typeof c.request==='string'&&c.request.length>30));
  assert(rubric.cases.every(c=>c.criteria.length>=3&&c.criteria.every(x=>typeof x==='string'&&x.length>10)));
  assert.equal(execution.actualGeneration,false);assert.equal(execution.rawResponsesEdited,false);
  assert.equal(execution.executions.length,2);
  assert.deepEqual(execution.executions.flatMap(x=>x.cases),ids);
  for(const f of execution.skillFiles)assert.equal(digest(f.path),f.sha256,'current evaluated skill changed: '+f.path);
  for(const run of execution.executions){
    assert(run.readFiles.includes('SKILL.md'));
    assert(!run.readFiles.some(p=>/rubric|outputs|review\.json/.test(p)),'executor read grading material');
    assert.deepEqual(run.outputs.map(x=>x.id),run.cases);
    for(const f of run.outputs){assert.equal(digest(f.path),f.sha256);assert(read(f.path).trim().length>60);}
  }
  assert.equal(review.method,'independent-text-review');assert.equal(review.actualGeneration,false);
  assert.deepEqual(review.cases.map(c=>c.id),ids);
  for(const c of review.cases){
    assert.equal(c.criteria.length,rubric.cases.find(r=>r.id===c.id).criteria.length,c.id);
    assert.equal(c.outputSha256,digest(c.output),c.id+' raw output changed');
    assert(c.criteria.every(x=>typeof x.pass==='boolean'&&typeof x.evidence==='string'&&x.evidence.length>10));
    assert.equal(c.pass,c.criteria.every(x=>x.pass),c.id);
  }
  assert.equal(review.passed,review.cases.flatMap(c=>c.criteria).filter(c=>c.pass).length);
  assert.equal(review.cases.filter(c=>c.pass).length,8,'independent review found failures');
  validateH3(read('tests/targeted-outputs/R02.md'),refFields);
});
console.log(JSON.stringify({status:'PASS',meaning:'Structure, formats and historical evidence integrity only; not current semantic or video acceptance',checks:checks.length,files:files.length,internalLinks,externalUrls:urls.size,details:checks,historicalEvaluation:{appliesToCurrentSkill:historicalSkillChanges.length===0,changedFiles:historicalSkillChanges}},null,2));

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
