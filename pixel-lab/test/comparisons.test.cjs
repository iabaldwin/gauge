const {test}=require('node:test');
const assert=require('node:assert/strict');
const {normalizeRequest,summarizeModels}=require('../pixel-lab.js');
const record=(id,original,candidates)=>normalizeRequest({id,original,candidates});
const original={model:'openai/reference',status:'succeeded',output:'grocery',durationMs:100};
const candidate={id:'qwen',provider:'Fireworks',model:'qwen',status:'succeeded',output:'grocery',durationMs:50,tokens:{output:15,reasoning:10,answer:5,nonReasoning:5}};
test('model summary separates coverage, scored outputs, paired latency, and reported tokens',()=>{
  const rows=[record('a',original,[candidate]),record('b',{...original,durationMs:300},[{...candidate,output:'other',durationMs:150,tokens:{output:9,reasoning:null}}]),record('c',original,[{...candidate,status:'skipped'}]),record('d',original,[{...candidate,status:'failed'}]),record('e',{...original,status:'failed'},[{...candidate,durationMs:900}])];
  const [s]=summarizeModels(rows);
  assert.equal(s.total,5);assert.equal(s.completed,3);assert.equal(s.skipped,1);assert.equal(s.failed,1);
  assert.equal(s.matches,1);assert.equal(s.scored,2);assert.equal(s.agreement,.5);
  assert.equal(s.latencySamples,2);assert.equal(s.latencyMedian,100);assert.equal(s.referenceLatencyMedian,200);
  assert.equal(s.answerMean,5);assert.equal(s.answerSamples,2);assert.equal(s.reasoningMean,10);assert.equal(s.reasoningSamples,2);
});
test('different reference models and multiple candidates remain distinct; unreported usage stays unknown',()=>{
  const rows=[record('a',original,[{...candidate,tokens:{}},{...candidate,id:'other',model:'other'}]),record('b',{...original,model:'openai/another'},[{...candidate,status:'pending'}])];
  const groups=summarizeModels(rows);
  assert.equal(groups.length,3);assert.equal(groups[0].answerMean,null);assert.equal(groups[0].reasoningMean,null);
  assert.equal(groups[2].pending,1);assert.equal(groups[2].agreement,null);assert.equal(groups[2].latencyMedian,null);
});
test('semantic agreement has its own denominator and abstentions never count as disagreement',()=>{
  const rows=[record('a',original,[{...candidate,output:'groceries',judgment:{status:'completed',verdict:'equivalent'}}]),record('b',original,[{...candidate,judgment:{status:'uncertain',verdict:'uncertain'}}]),record('c',original,[{...candidate,judgment:{status:'error'}}]),record('d',original,[{...candidate,output:'other',judgment:{status:'completed',verdict:'different'}}])];
  const [s]=summarizeModels(rows);assert.equal(s.semanticScored,2);assert.equal(s.semanticMatches,1);assert.equal(s.semanticAgreement,.5);assert.equal(s.semanticUncertain,1);assert.equal(s.scored,4);assert.equal(s.matches,2);
});
