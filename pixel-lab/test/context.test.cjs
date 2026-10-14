const {test}=require('node:test');
const assert=require('node:assert/strict');
const {normalizeContext}=require('../pixel-lab.js');

test('Chat preserves every ordered message, tool field, and request setting',()=>{
 const request={model:'ref',messages:[{role:'system',content:'Rules'},{role:'developer',content:'More rules'},{role:'user',content:'Look it up'},{role:'assistant',content:null,tool_calls:[{id:'call1',type:'function',function:{name:'lookup',arguments:'{"id":1}'}}]},{role:'tool',tool_call_id:'call1',content:'Found'},{role:'user',content:[{type:'text',text:'Describe it'},{type:'image_url',image_url:{url:'https://example.invalid/image',detail:'high'}}]}],tools:[{type:'function',function:{name:'lookup'}}],metadata:{custom:'preserved'},temperature:0};
 const before=JSON.stringify(request),context=normalizeContext(request);
 assert.deepEqual(context.entries.map(e=>e.label),['System','Developer','User','Assistant','Tool','User']);
 assert.equal(context.entries[3].fields.tool_calls[0].function.arguments,'{"id":1}');
 assert.equal(context.entries[4].fields.tool_call_id,'call1');
 assert.deepEqual(context.entries[5].content,request.messages[5].content);
 assert.deepEqual(context.settings,{model:'ref',tools:request.tools,metadata:{custom:'preserved'},temperature:0});
 assert.deepEqual(context.notices,[]);assert.equal(JSON.stringify(request),before);
});
test('Responses instructions, input order, call arguments and outputs remain distinct',()=>{
 const context=normalizeContext({instructions:'Use sources',input:[{role:'user',content:'Find it'},{type:'reasoning',summary:[{type:'summary_text',text:'Search first'}]},{type:'function_call',name:'search',call_id:'a',arguments:'invalid {'},{type:'function_call_output',call_id:'a',output:{answer:42}},{type:'custom_event',payload:{unknown:true}}],response_format:{type:'json_object'}});
 assert.deepEqual(context.entries.map(e=>e.label),['Instructions','User','Reasoning','Tool call','Tool result','Custom event']);
 assert.deepEqual(context.entries.map(e=>e.source),['instructions','input[0]','input[1]','input[2]','input[3]','input[4]']);
 assert.equal(context.entries[3].fields.arguments,'invalid {');
 assert.deepEqual(context.entries[4].fields.output,{answer:42});
 assert.deepEqual(context.entries[5].fields.payload,{unknown:true});
});
test('String, primitive, missing input and arbitrary object fields are not invented messages',()=>{
 assert.equal(normalizeContext('hello').entries[0].label,'Input');
 assert.equal(normalizeContext({input:'hello'}).entries[0].content,'hello');
 assert.equal(normalizeContext(null).entries[0].content,null);
 assert.deepEqual(normalizeContext().entries,[]);
 assert.deepEqual(normalizeContext({prompt:'custom',extra:[1,false]}).settings,{prompt:'custom',extra:[1,false]});
 const unusual=JSON.parse('{"__proto__":{"hidden":false},"input":[{"role":"__proto__","content":"text"}]}');
 assert.deepEqual(normalizeContext(unusual).settings.__proto__,{hidden:false});
 assert.equal(normalizeContext(unusual).entries[0].label,'__proto__');
});
test('Provider state and omitted captures are explicitly unavailable; no history synthesized',()=>{
 const c=normalizeContext({previous_response_id:'resp1',conversation:{id:'conv1'},input:[{type:'item_reference',id:'msg1'},{role:'user',content:'Now?'}]});
 assert.equal(c.entries.length,2);assert.equal(c.notices.length,3);
 assert.match(c.notices.join(' '),/not included/);
 assert.match(normalizeContext({capture_omitted:'request_too_large'}).notices[0],/request_too_large/);
});
