const {test} = require('node:test');
const assert = require('node:assert/strict');
const {normalizeRequest,comparisonStatus,summarize,diffLines} = require('../pixel-lab.js');
const request = candidates => normalizeRequest({id:'r',original:{model:'OpenAI',status:'succeeded'},candidates});
test('comparison failures never change original success or summary',()=>{
  const r=request([{id:'a',status:'failed',model:'a'},{id:'b',status:'skipped',model:'b'}]);
  assert.equal(r.original.status,'succeeded'); assert.equal(comparisonStatus(r),'mixed');
  assert.deepEqual(summarize([r]),{total:1,succeeded:1,captured:0,compared:0,skipped:0,failed:1});
});
test('multiple successful candidates count as one compared request',()=>{
  const r=request([{id:'a',status:'succeeded'},{id:'b',status:'succeeded'}]);
  assert.equal(comparisonStatus(r),'completed'); assert.equal(summarize([r]).compared,1);
});
test('all-skipped jobs and no configured jobs are distinct',()=>{
  assert.equal(comparisonStatus(request([])),'original');
  assert.equal(comparisonStatus(request([{id:'a',status:'skipped'}])),'skipped');
});
test('pending and running states take precedence over terminal states',()=>{
  assert.equal(comparisonStatus(request([{status:'succeeded'},{status:'pending'}])),'pending');
  assert.equal(comparisonStatus(request([{status:'failed'},{status:'running'}])),'running');
});
test('invalid ids rejected; timings sanitized; missing token counts stay unknown',()=>{
  assert.throws(()=>normalizeRequest({}),TypeError);
  assert.throws(()=>request([{id:'a'},{id:'a'}]),TypeError);
  const r=normalizeRequest({id:'r',original:{durationMs:-4},spans:[{id:'s',startMs:NaN,durationMs:-9}]});
  assert.equal(r.original.durationMs,0); assert.equal(r.original.tokens.reasoning,null); assert.equal(r.spans[0].durationMs,0);
});
test('diff reports changed and missing lines without interpreting correctness',()=>{
  assert.deepEqual(diffLines('a\nb','a\nc\nd'),[{original:'a',candidate:'a',changed:false},{original:'b',candidate:'c',changed:true},{original:'',candidate:'d',changed:true}]);
});
test('ES module provides the same named API',async()=>{
  const esm=await import('../pixel-lab.mjs'); assert.equal(typeof esm.Dashboard,'function'); assert.equal(esm.normalizeRequest,normalizeRequest);
});
