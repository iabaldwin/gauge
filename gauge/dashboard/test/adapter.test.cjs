const {test}=require('node:test');
const assert=require('node:assert/strict');
const {toRequest,tokens,pollingSource}=require('../gauge-adapter.js');
const row=()=>({capture:{call_id:'call_123',created_at:10,latency_ms:50,status:'completed',request:{model:'ref',messages:[{role:'user',content:'Soap'}]},response:{choices:[{message:{content:'household'}}],usage:{completion_tokens:8}}},comparison:{candidate:'qwen',status:'completed',received_at:10.1,started_at:10.2,finished_at:10.8,latency_ms:600,candidate_output:'household',agreement:true,usage:{completion_tokens:50,completion_tokens_details:{reasoning_tokens:42}}}});
test('joins original, candidate, usage and real spans by captured id',()=>{
  const r=toRequest(row());assert.equal(r.id,'call_123');assert.equal(r.original.output,'household');assert.equal(r.candidates[0].tokens.reasoning,42);assert.equal(r.spans.length,3);assert.equal(r.original.tokens.reasoning,null);
});
test('providers may omit usage or explicitly return null',()=>{
  assert.deepEqual(tokens(null),{input:null,output:null,reasoning:null,answer:null,nonReasoning:null,unattributed:null,sources:{},issues:[]});
  const data=row();data.comparison.usage=null;assert.equal(toRequest(data).candidates[0].tokens.output,null);
});
test('skipped and failed jobs never fabricate candidate outputs or successful original states',()=>{
  const data=row();data.comparison.status='skipped';data.comparison.reason='queue full';
  const r=toRequest(data);assert.equal(r.candidates[0].output,undefined);assert.equal(r.original.status,'succeeded');
  data.capture.status='error';assert.equal(toRequest(data).original.status,'failed');
});
test('multiple candidate jobs remain correlated to one request',()=>{
  const data=row(), first=data.comparison;delete data.comparison;
  data.comparisons=[first,{...first,candidate:'gpt-oss',candidate_output:'other'}];
  const r=toRequest(data);assert.equal(r.candidates.length,2);assert.equal(r.candidates[1].model,'gpt-oss');
  assert.equal(new Set(r.spans.map(s=>s.id)).size,r.spans.length);
});
test('Responses API text and token formats map correctly; historical captures have no invented job',()=>{
  const data=row();data.comparison=null;data.capture.response={output:[{type:'reasoning'},{type:'message',content:[{type:'output_text',text:'a'},{type:'output_text',text:'b'}]}],usage:{input_tokens:8,output_tokens:20,output_tokens_details:{reasoning_tokens:15}}};
  const r=toRequest(data);assert.equal(r.original.output,'a\nb');assert.equal(r.original.tokens.reasoning,15);assert.deepEqual(r.candidates,[]);assert.deepEqual(tokens(),{input:null,output:null,reasoning:null,answer:null,nonReasoning:null,unattributed:null,sources:{},issues:[]});
});
test('polling emits changed records only and disconnect cancels work',async()=>{
  let calls=0,updates=0;const states=[];
  const stop=pollingSource({token:'test',intervalMs:250,fetchImpl:async()=>{calls++;return {ok:true,json:async()=>({schema_version:1,records:[row()],stats:{}})};}})({upsert:()=>updates++,status:s=>states.push(s),error:e=>assert.fail(e)});
  await new Promise(r=>setTimeout(r,580));stop();const count=calls;await new Promise(r=>setTimeout(r,300));
  assert.ok(count>=2);assert.equal(calls,count);assert.equal(updates,1);assert.ok(states.includes('Connected · polling'));
});
test('backend errors are visible and disconnect stops retries',async()=>{
  const errors=[];let calls=0;const stop=pollingSource({intervalMs:250,fetchImpl:async()=>{calls++;return {status:503,ok:false};}})({upsert:()=>assert.fail(),status:()=>{},error:e=>errors.push(e.message)});
  await new Promise(r=>setTimeout(r,20));stop();assert.equal(calls,1);assert.match(errors[0],/HTTP 503/);
});
test('disconnect suppresses an in-flight response',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);let updates=0;
  const stop=pollingSource({token:'test',fetchImpl:()=>pending})({upsert:()=>updates++,status:()=>{},error:()=>assert.fail()});
  stop();resolve({ok:true,json:async()=>({schema_version:1,records:[row()]})});await new Promise(r=>setTimeout(r,0));assert.equal(updates,0);
});
test('polling loads older pages so early completed comparisons are not lost',async()=>{
  const older=row();older.capture.call_id='older';let calls=0;const ids=[];
  let finish;const done=new Promise(r=>finish=r);
  const stop=pollingSource({token:'test',url:'/v1/dashboard?run_id=r',fetchImpl:async url=>{
    calls++;return {ok:true,json:async()=>url.includes('before=')
      ?{schema_version:1,records:[older],next_cursor:null}
      :{schema_version:1,records:[row()],next_cursor:100,total:2}};
  },onSnapshot:s=>{assert.equal(s.loaded,2);finish();}})({upsert:r=>ids.push(r.id),status:()=>{},error:e=>assert.fail(e)});
  await done;stop();assert.equal(calls,2);assert.deepEqual(ids,['call_123','older']);
});
test('known incomplete outputs never become scored successes, but retain usage',()=>{
  const data=row();data.comparison.finish_reason='length';data.comparison.agreement=true;
  let r=toRequest(data);assert.equal(r.candidates[0].status,'skipped');
  assert.equal(r.candidates[0].output,undefined);assert.equal(r.candidates[0].grade,undefined);
  assert.equal(r.candidates[0].tokens.reasoning,42);assert.match(r.candidates[0].reason,/Incomplete/);
  data.capture.response.choices[0].finish_reason='length';
  r=toRequest(data);assert.equal(r.original.status,'skipped');assert.match(r.original.reason,/Incomplete/);
});
test('semantic judge replaces literal badge without replacing outputs or inference status',()=>{
  const data=row();data.comparison.candidate_output='household goods';data.comparison.agreement=false;
  data.comparison.judgment={status:'completed',verdict:'equivalent',confidence:.96,model:'jev',rubric_version:'v1',probabilities:{equivalent:.98,different:.01,uncertain:.01},answers:{original_compliance:{verdict:'compliant'},candidate_compliance:{verdict:'compliant'}}};
  let c=toRequest(data).candidates[0];assert.equal(c.status,'succeeded');assert.equal(c.grade.label,'Equivalent');assert.match(c.grade.explanation,/96%/);assert.match(c.grade.explanation,/Probabilities/);assert.match(c.grade.explanation,/Candidate compliance: compliant/);
  data.comparison.judgment={status:'error',error:'ReadTimeout'};
  c=toRequest(data).candidates[0];assert.equal(c.grade.label,'Unjudged');assert.equal(c.output,'household goods');assert.equal(c.status,'succeeded');
});
