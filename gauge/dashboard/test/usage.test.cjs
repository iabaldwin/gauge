const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const {normalizeUsage}=require('../../../pixel-lab/pixel-lab.js');
test('browser normalization agrees with Python for complete, missing, mixed, and inconsistent counters',()=>{
  const cases=[
    {usage:{completion_tokens:25,completion_tokens_details:{reasoning_tokens:20,text_tokens:1}},textOnly:true,answerText:'x'},
    {usage:{output_tokens:8,output_tokens_details:{reasoning_tokens:8}},textOnly:true,answerText:''},
    {usage:{completion_tokens:10,completion_tokens_details:{reasoning_tokens:2,text_tokens:4}},textOnly:false},
    {usage:{output_tokens:1,completion_tokens:2,input_tokens:false},textOnly:true},
    {usage:{output_tokens:3,output_tokens_details:{reasoning_tokens:4,text_tokens:5}},textOnly:true},
    {usage:{},textOnly:true,answerText:'unknown'}
  ];
  const script=`import json,sys\nfrom dataclasses import asdict\nfrom gauge.usage import normalize_usage\nrows=json.load(sys.stdin)\nprint(json.dumps([asdict(normalize_usage(x['usage'],text_only=x['textOnly'],answer_text=x.get('answerText'))) for x in rows]))`;
  const result=spawnSync(path.resolve(__dirname,'../../.venv/bin/python'),['-c',script],{input:JSON.stringify(cases),encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  const expected=JSON.parse(result.stdout),keys={input:'input_tokens',output:'total_output_tokens',reasoning:'reasoning_tokens',answer:'final_answer_tokens',nonReasoning:'non_reasoning_output_tokens',unattributed:'unattributed_output_tokens'};
  cases.forEach((c,i)=>{
    const actual=normalizeUsage(c.usage,{textOnly:c.textOnly,answerText:c.answerText});
    for(const [js,py] of Object.entries(keys))assert.equal(actual[js],expected[i][py]);
    assert.deepEqual(actual.issues,expected[i].issues);assert.deepEqual(actual.sources,expected[i].sources);
  });
});
