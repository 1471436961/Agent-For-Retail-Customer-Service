// Source-only uploader. Never imports student modules or installs dependencies.
import {readFile,readdir,lstat,mkdir,writeFile} from 'node:fs/promises';
import {resolve,relative,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {interactionPrinter, simulationSummary} from './interactions.mjs';
const showInteractions = interactionPrinter();
const root=fileURLToPath(new URL('..',import.meta.url)), args=process.argv.slice(2);
const flag=k=>{const i=args.indexOf(k);return i<0?null:args[i+1];};
const safe=v=>String(v??'').replace(/[\u0000-\u001f\u007f-\u009f]/g,' ');
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function collect(directory,base=directory){
  if((await lstat(directory)).isSymbolicLink()) throw Error('Symlinks are not accepted.');
  const files=[];
  for(const item of (await readdir(directory,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){
    if(item.isSymbolicLink()) throw Error('Symlinks are not accepted.');
    if(item.name.startsWith('.')||['node_modules','__pycache__','venv'].includes(item.name)) continue;
    const path=join(directory,item.name);
    if(item.isDirectory()) files.push(...await collect(path,base));
    else if(/\.(py|ts|js|mjs|md|txt|json|toml|yaml|yml)$/.test(item.name)){
      if(/^(credentials|secrets|id_rsa|id_ed25519)\./i.test(item.name)) throw Error('Remove credentials from agent/.');
      if((await lstat(path)).size>256*1024) throw Error('Source file exceeds 256 KiB.');
      files.push({path:relative(base,path).replaceAll('\\','/'),content:await readFile(path,'utf8')});
    }
    if(files.length>128) throw Error('At most 128 source files are accepted.');
  }
  return files;
}
async function main(){
  if(!process.env.HYPER_LAB_CONFIG) throw Error('Download Enterprise-AI.json from the permission center and set HYPER_LAB_CONFIG.');
  const configPath=resolve(process.env.HYPER_LAB_CONFIG);
  if(!relative(root,configPath).startsWith('..')) throw Error('Keep the personal config outside the repository.');
  const config=JSON.parse(await readFile(configPath,'utf8')),url=new URL(config.platformUrl);
  if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||url.pathname!=='/lab/api/enterprise-ai'||
    !['agentist.org','test.agentist.org','parallight-lab-git-staging-mrvgaos-projects.vercel.app'].includes(url.hostname)&&!/^parallight-[a-z0-9]+-mrvgaos-projects\.vercel\.app$/.test(url.hostname)) throw Error('Invalid platform URL.');
  const token=config.tokenFile?(await readFile(config.tokenFile,'utf8')).trim():config.token;
  if(!/^[\x21-\x7e]{32,512}$/.test(token||'')) throw Error('Invalid personal evaluation credential.');
  async function api(method,path,body,key){
    let response;
    try{response=await fetch(url.href+path,{method,redirect:'error',signal:AbortSignal.timeout(30000),headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json',...(key?{'Idempotency-Key':key}:{})},body:body?JSON.stringify(body):undefined});}
    catch{throw Error('Connection interrupted. Preserve the job ID or retry key; do not submit a new job blindly.');}
    if(!response.ok) throw Error(`Evaluation HTTP ${response.status}; 401: renew config; 403: access denied; 429: queue full; 503: service unavailable. Preserve the job ID/retry key.`);
    const chunks=[];let size=0;
    for await(const chunk of response.body){size+=chunk.length;if(size>4*1024*1024) throw Error('Response too large');chunks.push(chunk);}
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  }
  let job;const id=flag('--job-id');
  if(id){if(!/^[a-f0-9-]{36}$/.test(id)) throw Error('Invalid job ID');job=await api('GET','/v1/evaluations/'+id);}
  else{
    const files=await collect(join(root,'agent')),manifest=JSON.parse(files.find(f=>f.path==='agent.json')?.content||'{}');
    const payload={task:flag('--task')||'t1',domain:manifest.domain||'retail_plus',language:manifest.language||(files.some(f=>f.path==='agent.ts')?'typescript':'python'),source:'manual',files};
    if(Buffer.byteLength(JSON.stringify(payload))>2*1024*1024) throw Error('Submission exceeds 2 MiB.');
    const key=flag('--retry-key')||randomUUID();if(!/^[A-Za-z0-9_-]{16,100}$/.test(key)) throw Error('Invalid retry key');
    console.log(`任务 ${payload.task} · 上传源码\nIdempotency key: ${key}`);job=await api('POST','/v1/evaluations',payload,key);
  }
  if(!/^[a-f0-9-]{36}$/.test(job.job_id)) throw Error('Invalid server job ID');
  console.log(`Job: ${job.job_id}\n中断后继续查询：npm run evaluate -- --job-id ${job.job_id}`);
  const directory=join(root,'evaluation-results',job.job_id);await mkdir(directory,{recursive:true,mode:0o700});
  const save=()=>writeFile(join(directory,'report.json'),JSON.stringify(job,null,2)+'\n',{mode:0o600});
  const deadline=Date.now()+6*3600000;let previous='',last=0;
  while(true){
    await save();showInteractions(job);if(!['queued','running'].includes(job.status)) break;
    const state=`${job.status} ${job.active_case||''}`;
    if(state!==previous||Date.now()-last>15000){console.log(`${new Date().toLocaleTimeString()} ${job.status==='queued'?'排队中／环境自动准备':'执行中'} ${safe(job.active_case)}`);previous=state;last=Date.now();}
    if(Date.now()>=deadline) throw Error('Local wait ended. Query the same saved job ID later.');
    await new Promise(r=>setTimeout(r,2000));job=await api('GET','/v1/evaluations/'+job.job_id);
  }
  const report=job.report||{},cases=report.cases||[];
  if(report.simulation) console.log(simulationSummary(job));
  for(const item of cases) console.log(`${item.status==='passed'?'✓ PASS':item.status==='failed'?'✗ FAIL':'! ERROR'} ${safe(item.case_id)}${item.feedback?' — '+safe(item.feedback):''}`);
  console.log(`\n${job.status==='done'?'结果':'未评分'}：${report.passed??cases.filter(c=>c.status==='passed').length}/${report.total??cases.length} cases 通过`);
  const html=`<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Enterprise AI 评测</title><style>body{max-width:960px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f7;padding:20px}summary{padding:12px;cursor:pointer}</style><h1>Enterprise AI · ${escape(report.verdict||job.status)}</h1><p>Job ${escape(job.job_id)}</p>${cases.map(c=>`<details ${c.status==='passed'?'':'open'}><summary>${escape(c.status)} · ${escape(c.case_id)}</summary><pre>${escape(JSON.stringify(c,null,2))}</pre></details>`).join('')}<details><summary>完整记录</summary><pre>${escape(JSON.stringify(job,null,2))}</pre></details></html>`;
  await writeFile(join(directory,'report.html'),html,{mode:0o600});console.log(`本地网页：${join(directory,'report.html')}`);
  if(job.web_url) console.log(`在线结果：${safe(job.web_url)}`);
  process.exitCode=job.status!=='done'?2:report.verdict==='passed'?0:1;
}
main().catch(error=>{console.error(error.code?'Cannot read local configuration/source or save results.':error.message);process.exitCode=2;});
