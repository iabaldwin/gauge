const {test}=require('node:test');
const assert=require('node:assert/strict');
const {normalizeUsage}=require('../pixel-lab.js');
test('answer counters and non-reasoning tokens remain distinct',()=>{
  const usage=normalizeUsage({completion_tokens:25,completion_tokens_details:{reasoning_tokens:20,text_tokens:1}},{textOnly:true,answerText:'x'});
  assert.equal(usage.answer,1);assert.equal(usage.nonReasoning,5);assert.equal(usage.unattributed,4);
  assert.equal(normalizeUsage({completion_tokens:25,completion_tokens_details:{reasoning_tokens:20}}).answer,null);
});
