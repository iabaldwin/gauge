/* Synthetic local demo. No API calls, keys, fonts, or external resources. */
(function () {
  const base = Date.UTC(2026,9,13,18,24);
  const names = ['Eggs','Shampoo','Bananas','Toothpaste','Coffee','Dish soap','Apples','Paper towels'];
  const category = name => ['Eggs','Bananas','Coffee','Apples'].includes(name) ? 'grocery' : 'household';
  function makeRequest(name, i, mode = 'original') {
    const output = { category: category(name) }, duration = 340+i*31;
    const candidate = (id, model, offset) => ({ id, provider:'Fireworks', model, status: mode === 'skipped' ? 'skipped' : 'succeeded',
      ...(mode === 'skipped' ? {reason:'Comparison queue full'} : {output, durationMs:510+offset, tokens:{input:48,output:82+offset,reasoning:74+offset},grade:{label:'Exact match',explanation:'Matches the original label. This does not establish correctness.'}}) });
    return { id:`req_${String(i+1).padStart(4,'0')}`, name, startedAt:base+i*3000, captured:mode !== 'original', input:{messages:[{role:'user',content:`Classify this item as grocery or household: ${name}`}]},
      original:{model:'OpenAI / reference',status:'succeeded',output,durationMs:duration,tokens:{input:42,output:8,reasoning:0}},
      candidates:mode === 'original' ? [] : [candidate('qwen','Qwen / candidate A',0),candidate('oss','GPT-OSS / candidate B',90)],
      spans:[{id:'original',name:'OpenAI',kind:'original',startMs:0,durationMs:duration,status:'succeeded'},
        ...(mode === 'original' ? [] : [{id:'capture',name:'Capture',kind:'capture',startMs:duration+2,durationMs:12,status:'succeeded'},
          {id:'queue',name:'Backend queue',kind:'queue',startMs:duration+14,durationMs:mode === 'skipped' ? 8 : 65,status:mode === 'skipped' ? 'skipped' : 'succeeded'}]),
        ...(mode === 'compared' ? [{id:'qwen',name:'Qwen',kind:'candidate',startMs:duration+79,durationMs:510,status:'succeeded'},
          {id:'oss',name:'GPT-OSS',kind:'candidate',startMs:duration+79,durationMs:600,status:'succeeded'},
          {id:'grade',name:'Exact-match grader',kind:'grade',startMs:duration+679,durationMs:23,status:'succeeded'}] : [])] };
  }
  let sequence = 24;
  const requests = names.map((name,i) => makeRequest(name,i,i<4 ? 'original' : i<6 ? 'compared' : 'skipped'));
  const demoTimers = new Set();
  const delay = ms => new Promise(resolve => { const t = setTimeout(() => { demoTimers.delete(t); resolve(); }, ms); demoTimers.add(t); });
  window.demo = new PixelLab.Dashboard('#app', {
    title:'Gauge', subtitle:'LOCAL DEMO / synthetic data', requests, maxRequests:100,
    async onRun(dashboard) {
      const r = makeRequest(['Olive oil','Laundry soap','Green tea'][sequence%3],sequence++,'compared');
      r.original.status='running'; delete r.original.output; r.original.durationMs=null; r.original.tokens={};
      r.candidates.forEach(c => { c.status='pending'; delete c.output; delete c.grade; c.durationMs=null; c.tokens={}; }); r.spans=[];
      dashboard.upsert(r); dashboard.select(r.id);
      await delay(650);
      const finished = makeRequest(r.name,sequence-1,'compared');
      dashboard.upsert({...finished,candidates:r.candidates.map(c=>({...c,status:'running'})),spans:finished.spans.slice(0,3)});
      await delay(1000); dashboard.upsert(finished);
    }
  });
  window.demo.select('req_0006');
  const benchmark = Array.from({length:24}, (_, i) => {
    const r = makeRequest(names[i%names.length], i, i>21 ? 'skipped' : 'compared');
    r.original.tokens.answer = 8;
    r.original.tokens.nonReasoning = 8;
    r.original.tokens.unattributed = 0;
    r.candidates.forEach((c, index) => {
      if (c.status !== 'succeeded') return;
      c.durationMs = 240 + ((i*137 + index*211)%900);
      c.tokens.answer = 8;
      c.tokens.nonReasoning = 8;
      c.tokens.unattributed = 0;
      if (i%5 === 0 && index === 1) {
        c.output = {category:category(r.name)==='grocery' ? 'household' : 'grocery'};
        c.grade = {label:'Different output',explanation:'The labels differ. Human review is needed to establish correctness.'};
      }
      if(i<20){
        const different=i%5===0 && index===1,uncertain=i===3 && index===0;
        c.judgment={status:uncertain?'uncertain':'completed',verdict:uncertain?'uncertain':different?'different':'equivalent',confidence:uncertain?.46:.95,probabilities:{equivalent:different?.03:.97,different:different?.97:.03,uncertain:0},model:'Demo judge',rubric_version:'synthetic-v1'};
        c.grade={label:uncertain?'Uncertain':different?'Different':'Equivalent',explanation:'Synthetic semantic judgment for the component demo. No model was called.'};
      }
      if (i===20 && index===0) { c.status='failed';c.error='Synthetic provider timeout';delete c.output;delete c.grade;c.tokens={};c.durationMs=null; }
      if (i===21) { c.status='pending';delete c.output;delete c.grade;c.tokens={};c.durationMs=null; }
    });
    return r;
  });
  window.comparisonDemo = new PixelLab.Dashboard('#comparison-app', {
    title:'Gauge',subtitle:'MODEL COMPARISONS / synthetic data',layout:'comparisons',requests:benchmark,maxRequests:100,
    onRun: window.demo.options.onRun
  });
  // The independent inspector demonstrates a complete request, including tool context.
  benchmark[0]={...benchmark[0],input:{
    model:'OpenAI / reference',
    messages:[
      {role:'system',content:'Classify shopping items as grocery or household. Return only the category.'},
      {role:'developer',content:'Use the catalogue when an item is ambiguous. Preserve the supplied product description.'},
      {role:'user',content:'I am sorting my shopping list. Can you check the carton of eggs?'},
      {role:'assistant',content:null,tool_calls:[{id:'lookup_01',type:'function',function:{name:'lookup_product',arguments:'{"query":"carton of eggs"}'}}]},
      {role:'tool',tool_call_id:'lookup_01',content:'{"name":"Eggs","department":"Fresh food","pack_size":12}'},
      {role:'assistant',content:'The catalogue lists these eggs under Fresh food.'},
      {role:'user',content:[{type:'text',text:'Which category should I put them in?'}]}
    ],
    tools:[{type:'function',function:{name:'lookup_product',description:'Look up a product in the local catalogue.',parameters:{type:'object',properties:{query:{type:'string'}},required:['query'],additionalProperties:false}}}],
    temperature:0
  }};
  const inspector = new PixelLab.OutputInspector('#standalone-inspector');
  const selectInspection = id => {
    inspector.update(PixelLab.normalizeRequest(benchmark.find(r=>r.id===id)), 'oss');
    explorer.update(benchmark.map(PixelLab.normalizeRequest), id);
  };
  const explorer = new PixelLab.RequestExplorer('#inspector-explorer', {onSelect:selectInspection});
  explorer.update(benchmark.map(PixelLab.normalizeRequest), 'req_0001');
  selectInspection('req_0001');

  const tabs = [...document.querySelectorAll('.demo-nav [role=tab]')];
  function activate(id, focus = false) {
    const selected = tabs.find(tab=>tab.getAttribute('aria-controls')===id) || tabs[0];
    for (const tab of tabs) {
      const active = tab===selected;
      tab.setAttribute('aria-selected', String(active));tab.tabIndex=active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden=!active;
    }
    if (focus) selected.focus();
    if (selected.getAttribute('aria-controls')==='workspace') window.demo.trace.fit();
  }
  for (const tab of tabs) {
    tab.addEventListener('click', ()=>{
      const id=tab.getAttribute('aria-controls');
      history.replaceState(null,'','#'+id);activate(id);
    });
    tab.addEventListener('keydown', event=>{
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const index=event.key==='Home' ? 0 : event.key==='End' ? tabs.length-1 : (tabs.indexOf(tab)+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      tabs[index].click();tabs[index].focus();
    });
  }
  window.addEventListener('hashchange', ()=>activate(location.hash.slice(1)));
  activate(location.hash.slice(1));
  window.addEventListener('pagehide', () => {
    demoTimers.forEach(clearTimeout);window.demo.destroy();window.comparisonDemo.destroy();explorer.destroy();inspector.destroy();
  }, {once:true});
})();
