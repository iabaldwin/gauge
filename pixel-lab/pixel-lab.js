/* Pixel Lab — no dependencies, no network requests, no global DOM mutations. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.PixelLab = api;
})(globalThis, function () {
  'use strict';
  const STATES = new Set(['pending', 'running', 'succeeded', 'failed', 'skipped']);
  const LABELS = { pending: 'Pending', running: 'Running', succeeded: 'Succeeded', failed: 'Failed', skipped: 'Skipped', completed: 'Compared', original: 'Original only', mixed: 'Partial' };
  const finite = (v, fallback = 0) => Number.isFinite(v) && v >= 0 ? v : fallback;
  const stringify = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ms = value => value == null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  const badge = (status, text) => `<span class="pl-badge pl-${esc(status)}"><i></i>${esc(text || LABELS[status] || status)}</span>`;
  const icon = name => `<span class="pl-icon" aria-hidden="true">${({ app: '▦', model: '✳', response: '▤', capture: '▥', queue: '▣', compare: '⚖', trace: '⌘' })[name] || '▪'}</span>`;
  function result(value = {}) {
    const status = STATES.has(value.status) ? value.status : 'pending';
    return { ...value, status, model: String(value.model || 'Unknown model'), durationMs: value.durationMs == null ? null : finite(value.durationMs),
      tokens: { input: null, output: null, reasoning: null, answer: null, nonReasoning: null, unattributed: null, ...value.tokens } };
  }
  function normalizeRequest(value) {
    if (!value || typeof value.id !== 'string' || !value.id.trim()) throw new TypeError('Each request needs a nonempty string id');
    const candidates = (value.candidates || []).map((item, i) => ({ ...result(item), id: String(item.id ?? `candidate-${i}`) }));
    if (new Set(candidates.map(c => c.id)).size !== candidates.length) throw new TypeError('Candidate ids must be unique within a request');
    return { ...value, id: value.id, name: String(value.name || value.id), startedAt: finite(value.startedAt), original: result(value.original), candidates,
      captured: Boolean(value.captured), spans: (value.spans || []).map(s => ({ ...s, id: String(s.id), name: String(s.name || s.id), startMs: finite(s.startMs), durationMs: finite(s.durationMs), status: STATES.has(s.status) ? s.status : 'succeeded' })) };
  }
  function comparisonStatus(request) {
    const c = request.candidates;
    if (!c.length) return 'original';
    if (c.some(x => x.status === 'running')) return 'running';
    if (c.some(x => x.status === 'pending')) return 'pending';
    if (c.every(x => x.status === 'succeeded')) return 'completed';
    if (c.every(x => x.status === 'skipped')) return 'skipped';
    if (c.every(x => x.status === 'failed')) return 'failed';
    return 'mixed';
  }
  function summarize(requests) {
    return { total: requests.length, succeeded: requests.filter(r => r.original.status === 'succeeded').length,
      captured: requests.filter(r => r.captured).length, compared: requests.filter(r => r.candidates.some(c => c.status === 'succeeded')).length,
      skipped: requests.filter(r => r.candidates.length && r.candidates.every(c => c.status === 'skipped')).length,
      failed: requests.filter(r => r.candidates.some(c => c.status === 'failed')).length };
  }
  // Aligned, line-by-line comparison. No semantic correctness is inferred.
  function diffLines(original, candidate) {
    const a = stringify(original).split('\n'), b = stringify(candidate).split('\n');
    return Array.from({ length: Math.max(a.length, b.length) }, (_, i) => ({ original: a[i] ?? '', candidate: b[i] ?? '', changed: a[i] !== b[i] }));
  }
  function target(el) {
    const node = typeof el === 'string' ? document.querySelector(el) : el;
    if (!node || node.nodeType !== 1) throw new TypeError('Provide a mount element or valid selector');
    return node;
  }
  const known = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const median = values => {
    const sorted = values.filter(known).sort((a,b) => a-b), n = sorted.length;
    return n ? (sorted[Math.floor((n-1)/2)] + sorted[Math.floor(n/2)]) / 2 : null;
  };
  const mean = values => { const v = values.filter(known); return v.length ? v.reduce((a,b) => a+b,0)/v.length : null; };
  const answerTokens = item => known(item.tokens?.answer) ? item.tokens.answer : null;
  /** Non-reasoning tokens can include formatting or other output channels. */
  function normalizeUsage(usage = {}, {textOnly=false, answerText} = {}) {
    usage=usage || {};const sources={},issues=[];
    function counter(metric,...paths){
      const found=[];
      for(const path of paths){
        const value=path.split('.').reduce((v,key)=>v && typeof v==='object'?v[key]:undefined,usage);
        if(value==null)continue;
        if(!Number.isInteger(value) || value<0){issues.push(`Invalid counter: ${path}`);continue;}
        found.push([path,value]);
      }
      if(!found.length)return null;
      if(new Set(found.map(([,v])=>v)).size!==1){issues.push(`Conflicting counters: ${metric}`);return null;}
      sources[metric]=found.map(([p])=>p).join(', ');return found[0][1];
    }
    const input=counter('input_tokens','input_tokens','prompt_tokens');
    const output=counter('total_output_tokens','output_tokens','completion_tokens');
    const reasoning=counter('reasoning_tokens','output_tokens_details.reasoning_tokens','completion_tokens_details.reasoning_tokens');
    let answer=null,nonReasoning=null,unattributed=null;
    if(textOnly){
      answer=counter('final_answer_tokens','output_tokens_details.text_tokens','completion_tokens_details.text_tokens');
      if(answer===null && answerText==='' && !issues.length){answer=0;sources.final_answer_tokens='empty_answer_channel';}
    }
    if(output!==null && reasoning!==null){
      if(reasoning<=output){nonReasoning=output-reasoning;sources.non_reasoning_output_tokens='total_output_tokens - reasoning_tokens';}
      else issues.push('reasoning_tokens exceeds total_output_tokens');
    }
    if(output!==null && answer!==null && answer>output)issues.push('final_answer_tokens exceeds total_output_tokens');
    if(nonReasoning!==null && answer!==null){
      if(answer<=nonReasoning){unattributed=nonReasoning-answer;sources.unattributed_output_tokens='total_output_tokens - reasoning_tokens - final_answer_tokens';}
      else issues.push('reasoning_tokens + final_answer_tokens exceeds total_output_tokens');
    }
    return {input,output,reasoning,answer,nonReasoning,unattributed,sources,issues};
  }
  function evaluation(candidate) {
    if(candidate?.grade)return candidate.grade;
    const j=candidate?.judgment;if(!j)return null;
    const label=({completed:j.verdict==='equivalent'?'Equivalent':'Different',uncertain:'Uncertain',pending:'Judging',running:'Judging'})[j.status] || 'Unjudged';
    return {label,explanation:`${j.model || 'Backend judge'}${known(j.confidence)?` · ${(j.confidence*100).toFixed(0)}% confidence`:''}${j.probabilities?` · Probabilities: ${JSON.stringify(j.probabilities)}`:''}. Semantic comparison, not a correctness score.`};
  }
  function outputAgreement(request, candidate) {
    if (request.original.status !== 'succeeded' || candidate.status !== 'succeeded' || request.original.output === undefined || candidate.output === undefined) return null;
    return stringify(request.original.output) === stringify(candidate.output);
  }
  /** Aggregate each candidate against its original model. Failed and skipped jobs never enter quality or latency averages. */
  function summarizeModels(requests) {
    const groups = new Map();
    for (const r of requests) for (const c of r.candidates) {
      const key = JSON.stringify([r.original.model,c.provider || '',c.model]);
      if (!groups.has(key)) groups.set(key,{key,model:c.model,provider:c.provider || '',reference:r.original.model,total:0,completed:0,failed:0,skipped:0,pending:0,running:0,matches:0,scored:0,semanticMatches:0,semanticScored:0,semanticUncertain:0,latencies:[],referenceLatencies:[],answer:[],reasoning:[],nonReasoning:[],output:[]});
      const g = groups.get(key);g.total++;
      if (c.status !== 'succeeded') { g[c.status]++;continue; }
      g.completed++;
      const agreement = outputAgreement(r,c);
      if (agreement !== null) { g.scored++;if(agreement)g.matches++; }
      if(r.original.status==='succeeded' && c.judgment?.status==='completed' && ['equivalent','different'].includes(c.judgment.verdict)){
        g.semanticScored++;if(c.judgment.verdict==='equivalent')g.semanticMatches++;
      }else if(c.judgment?.status==='uncertain')g.semanticUncertain++;
      if (r.original.status === 'succeeded' && known(r.original.durationMs) && known(c.durationMs)) {
        g.latencies.push(c.durationMs);g.referenceLatencies.push(r.original.durationMs);
      }
      g.answer.push(answerTokens(c));g.reasoning.push(c.tokens?.reasoning);g.nonReasoning.push(c.tokens?.nonReasoning);g.output.push(c.tokens?.output);
    }
    return [...groups.values()].map(g=>({key:g.key,model:g.model,provider:g.provider,reference:g.reference,total:g.total,completed:g.completed,failed:g.failed,skipped:g.skipped,pending:g.pending,running:g.running,matches:g.matches,scored:g.scored,
      agreement:g.scored ? g.matches/g.scored : null,semanticMatches:g.semanticMatches,semanticScored:g.semanticScored,semanticUncertain:g.semanticUncertain,semanticAgreement:g.semanticScored?g.semanticMatches/g.semanticScored:null,latencySamples:g.latencies.length,latencyMedian:median(g.latencies),referenceLatencyMedian:median(g.referenceLatencies),
      answerMean:mean(g.answer),answerSamples:g.answer.filter(known).length,reasoningMean:mean(g.reasoning),reasoningSamples:g.reasoning.filter(known).length,nonReasoningMean:mean(g.nonReasoning),nonReasoningSamples:g.nonReasoning.filter(known).length,outputMean:mean(g.output),outputSamples:g.output.filter(known).length}));
  }
  class Component {
    constructor(el, options = {}) {
      this.el = target(el); this.options = options; this.cleanups = []; this.destroyed = false;
      this.addedRoot = !this.el.classList.contains('pl'); this.el.classList.add('pl');
    }
    listen(type, callback) { this.el.addEventListener(type, callback); this.cleanups.push(() => this.el.removeEventListener(type, callback)); }
    destroy() { this.destroyed = true; this.cleanups.forEach(f => f()); this.cleanups = []; this.el.replaceChildren(); if (this.addedRoot) this.el.classList.remove('pl'); }
  }
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const fieldName = name => String(name).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
  const roleNames = {system:'System',developer:'Developer',user:'User',assistant:'Assistant',tool:'Tool',function:'Function'};
  /** Describe the captured request without resolving remote state or discarding unknown fields. */
  function normalizeContext(input) {
    const entries = [], notices = [];
    let settings = {};
    function append(value, source) {
      if (!isObject(value)) { entries.push({label:'Input',role:'input',source,content:value,fields:{}}); return; }
      const {role,content,...fields} = value;
      const type = value.type;
      const itemNames={function_call:'Tool call',function_call_output:'Tool result',item_reference:'Referenced item',reasoning:'Reasoning'};
      const label = (Object.hasOwn(roleNames,role)?roleNames[role]:role) || (Object.hasOwn(itemNames,type)?itemNames[type]:null) || fieldName(type || 'Input item');
      entries.push({label:String(label),role:role?String(role):(type==='function_call' || type==='function_call_output'?'tool':'input'),source,content,fields});
      if (type==='item_reference') notices.push(`Referenced item at ${source}: its contents are not included in this captured request.`);
    }
    if (isObject(input)) {
      for (const key of ['instructions','system']) if (Object.hasOwn(input,key)) entries.push({label:key==='system'?'System':'Instructions',role:'system',source:key,content:input[key],fields:{}});
      for (const key of ['messages','input']) if (Object.hasOwn(input,key)) {
        const value=input[key];
        if (Array.isArray(value)) value.forEach((item,i)=>append(item,`${key}[${i}]`));
        else append(value,key);
      }
      settings=Object.fromEntries(Object.entries(input).filter(([key])=>!['instructions','system','messages','input'].includes(key)));
      if (input.previous_response_id) notices.push('previous_response_id refers to earlier provider state. That earlier context is not included here unless it is also present in the captured input.');
      if (input.conversation) notices.push('conversation refers to server-held context. Only content included in this captured request is shown.');
      if (input.capture_omitted) notices.push(`Request body was not captured: ${stringify(input.capture_omitted)}.`);
    } else if (input !== undefined) append(input,'input');
    return {entries,settings,notices};
  }
  function contextFields(fields) {
    return Object.entries(fields).map(([key,value])=>`<div class="pl-context-field"><h4>${esc(fieldName(key))}</h4>${contextValue(value,key)}</div>`).join('');
  }
  function contextValue(value, field='') {
    if (value === undefined) return '';
    if (isObject(value) && ['parameters','schema','response_format'].includes(field)) return `<pre tabindex="0">${esc(stringify(value))}</pre>`;
    if (Array.isArray(value)) return value.length ? value.map((part,i)=>`<div class="pl-context-part">${isObject(part)?`<span class="pl-context-kind">${esc(part.type || `Part ${i+1}`)}</span>${contextFields(part)}`:contextValue(part,field)}</div>`).join('') : '<pre>[]</pre>';
    if (isObject(value)) return contextFields(value) || '<pre>{}</pre>';
    let text=stringify(value);
    if (field==='arguments' && typeof value==='string') { try { text=JSON.stringify(JSON.parse(value),null,2); } catch { /* Preserve malformed arguments as received. */ } }
    return `<pre tabindex="0">${esc(text)}</pre>`;
  }
  function contextMarkup(input) {
    const {entries,settings,notices}=normalizeContext(input);
    const tools=Object.fromEntries(Object.entries(settings).filter(([key])=>key==='tools' || key==='functions'));
    const options=Object.fromEntries(Object.entries(settings).filter(([key])=>key!=='tools' && key!=='functions'));
    return `<section class="pl-context" aria-label="Captured request context"><header class="pl-context-heading"><div><h3>Request context</h3><p>Captured input in request order · generated outputs are shown separately.</p></div><span>${entries.length} ${entries.length===1?'entry':'entries'}</span></header>${notices.map(note=>`<p class="pl-context-notice" role="note">${esc(note)}</p>`).join('')}${entries.length?`<ol class="pl-context-list">${entries.map((entry,i)=>`<li class="pl-context-entry" data-role="${['system','developer','user','assistant','tool','function'].includes(entry.role)?entry.role:'input'}"><header><span class="pl-context-number">${String(i+1).padStart(2,'0')}</span><strong>${esc(entry.label)}</strong><code>${esc(entry.source)}</code></header><div class="pl-context-body">${contextValue(entry.content)}${contextFields(entry.fields)}</div></li>`).join('')}</ol>`:'<p class="pl-empty">No inline context was captured. Any supplied fields are shown below.</p>'}${Object.keys(tools).length?`<section class="pl-context-tools"><h3>Available tools</h3>${contextFields(tools)}</section>`:''}${Object.keys(options).length?`<details class="pl-context-settings"><summary>Request settings & other fields · ${Object.keys(options).length}</summary>${contextFields(options)}</details>`:''}</section>`;
  }
  class RequestContext extends Component {
    constructor(el,options={}) { super(el,options);this.update(options.input); }
    update(input) { if (!this.destroyed) this.el.innerHTML=contextMarkup(input); }
  }
  function binHistogram(series, binCount=8) {
    if(!Number.isInteger(binCount) || binCount<2 || binCount>32)throw new TypeError('Histogram bins must be an integer between 2 and 32');
    const ids=new Set();let largest=0;
    const rows=series.map((s,index)=>{
      if(!s || typeof s.id!=='string' || ids.has(s.id) || !Array.isArray(s.values))throw new TypeError('Histogram series need unique ids and numeric value arrays');
      ids.add(s.id);const values=s.values.filter(known);for(const value of values)largest=Math.max(largest,value);
      const sorted=[...values].sort((a,b)=>a-b);
      const quantile=p=>{if(!sorted.length)return null;const position=(sorted.length-1)*p,i=Math.floor(position);return sorted[i]+(sorted[Math.min(i+1,sorted.length-1)]-sorted[i])*(position-i);};
      return {id:s.id,label:String(s.label || s.id),color:['blue','purple','mint','orange'].includes(s.color)?s.color:['blue','purple','mint','orange'][index%4],values,excluded:s.values.length-values.length,median:quantile(.5),p95:quantile(.95),counts:Array(binCount).fill(0)};
    });
    const power=10**Math.floor(Math.log10(largest || 1));
    const factor=[1,2,2.5,5,10].find(n=>n>=largest/power) || 10;
    const proposed=factor*power;
    const max=largest===0?1:Number.isFinite(proposed) && proposed>=largest?proposed:largest;
    const bins=Array.from({length:binCount},(_,index)=>({start:max*(index/binCount),end:max*((index+1)/binCount)}));
    for(const row of rows)for(const value of row.values)row.counts[Math.min(binCount-1,Math.floor(value/max*binCount))]++;
    return {max,bins,series:rows};
  }
  class Histogram extends Component {
    constructor(el,options={}) {
      super(el,options);this.el.classList.add('pl-histogram');this.series=[];this.hiddenSeries=new Set();this.bins=options.bins??8;this.signature='';
      this.listen('click',event=>{
        const toggle=event.target.closest('[data-hist-series]');if(!toggle)return;
        const id=toggle.dataset.histSeries;
        if(this.hiddenSeries.has(id))this.hiddenSeries.delete(id);else this.hiddenSeries.add(id);
        this.render();this.el.querySelectorAll('[data-hist-series]').forEach(button=>{if(button.dataset.histSeries===id)button.focus();});
      });
      const inspect=event=>{
        const bar=event.target.closest('[data-hist-bin]');
        if(bar)this.el.querySelector('.pl-hist-tooltip').textContent=bar.getAttribute('aria-label');
      };
      this.listen('pointerover',inspect);this.listen('focusin',inspect);
      this.update(options.series || []);
    }
    update(series) {
      if(this.destroyed)return;
      const signature=JSON.stringify([series,this.bins]);if(signature===this.signature)return;
      binHistogram(series,this.bins);this.signature=signature;this.series=series;this.render();
    }
    setBins(bins) { binHistogram(this.series,bins);this.bins=bins;this.signature='';this.update(this.series); }
    format(value) {
      if(value===null)return '—';
      if(this.options.unit==='ms')return ms(value);
      return `${Number(value.toFixed(2)).toLocaleString()}${this.options.unit?' '+this.options.unit:''}`;
    }
    render() {
      const data=binHistogram(this.series,this.bins);
      const visible=data.series.filter(s=>!this.hiddenSeries.has(s.id));
      const sampleCount=visible.reduce((n,s)=>n+s.values.length,0), peak=Math.max(1,...visible.flatMap(s=>s.counts));
      const left=34,top=16,width=412,height=148,bottom=top+height;
      const slot=width/this.bins,groupWidth=slot*.76,barWidth=groupWidth/Math.max(visible.length,1);
      const guides=[0,.5,1].map(f=>`<line class="pl-hist-grid-line" x1="${left}" x2="${left+width}" y1="${bottom-height*f}" y2="${bottom-height*f}"/><text class="pl-hist-axis" x="${left-9}" y="${bottom-height*f+4}" text-anchor="end">${Number((peak*f).toFixed(1))}</text>`).join('');
      const bars=visible.map(s=>s.counts.map((count,index)=>{
        if(!count)return '';
        const x=left+slot*index+(slot-groupWidth)/2+visible.indexOf(s)*barWidth;
        const h=count/peak*height;
        const range=`${this.format(data.bins[index].start)} to ${this.format(data.bins[index].end)}${index===this.bins-1?' inclusive':' (upper bound excluded)'}`;
        return `<rect class="pl-hist-bar pl-hist-${s.color}" x="${x}" y="${bottom-h}" width="${Math.max(.5,barWidth-2)}" height="${h}" tabindex="0" role="img" data-hist-bin="${index}" aria-label="${esc(s.label)} · ${esc(range)} · ${count} of ${s.values.length} measurements"/>`;
      }).join('')).join('');
      const labels=[0,.5,1].map(f=>`<text class="pl-hist-axis" x="${left+width*f}" y="${bottom+22}" text-anchor="${f===0?'start':f===1?'end':'middle'}">${esc(this.format(data.max*f))}</text>`).join('');
      const chart=sampleCount?`<svg class="pl-hist-svg" viewBox="0 0 470 195" role="group" aria-label="${esc(this.options.title || 'Histogram')} bins"><text class="pl-hist-axis" x="${left}" y="9">COUNT</text>${guides}${bars}${labels}</svg>`:`<div class="pl-hist-empty">${data.series.some(s=>s.values.length)?'Select a model in the legend to show its distribution.':'No reported measurements for this comparison.'}</div>`;
      this.el.innerHTML=`<header class="pl-hist-heading"><h3>${esc(this.options.title || 'Histogram')}</h3><span>${sampleCount} measurements</span></header>${this.options.subtitle?`<p class="pl-hist-subtitle">${esc(this.options.subtitle)}</p>`:''}${chart}<div class="pl-hist-legend">${data.series.map(s=>`<button data-hist-series="${esc(s.id)}" aria-pressed="${!this.hiddenSeries.has(s.id)}" class="pl-hist-${s.color}"><i aria-hidden="true"></i><span>${esc(s.label)}<small>n=${s.values.length} · median ${esc(this.format(s.median))} · p95 ${esc(this.format(s.p95))}</small></span></button>`).join('')}</div><div class="pl-hist-tooltip" role="status">Hover or focus a bar for its range and count.</div>`;
    }
    destroy() { this.el.classList.remove('pl-histogram');super.destroy(); }
  }
  function comparisonDistributions(requests, metric, model='') {
    const groups=new Map(), originals=new Set();
    const measurement=item=>metric==='latency'?item.durationMs:item.tokens?.[metric];
    for(const r of requests)for(const c of r.candidates){
      if(r.original.status!=='succeeded' || c.status!=='succeeded' || (model && c.model!==model))continue;
      const original=measurement(r.original),candidate=measurement(c);
      if(metric==='latency' && (!known(original) || !known(candidate)))continue;
      const originalKey=JSON.stringify(['original',r.original.model]);
      const candidateKey=JSON.stringify(['candidate',c.provider || '',c.model]);
      if(!groups.has(originalKey))groups.set(originalKey,{id:originalKey,label:'Original · '+r.original.model.split('/').pop(),color:'blue',values:[]});
      if(!groups.has(candidateKey))groups.set(candidateKey,{id:candidateKey,label:c.model.split('/').pop(),values:[]});
      const requestKey=JSON.stringify([r.id,r.original.model]);
      if(!originals.has(requestKey)){groups.get(originalKey).values.push(original);originals.add(requestKey);}
      groups.get(candidateKey).values.push(candidate);
    }
    return [...groups.values()];
  }
  class ComparisonSummary extends Component {
    constructor(el, options = {}) {
      super(el,options);this.requests=[];this.query='';this.model='';this.filter='completed';this.limit=100;
      this.el.classList.add('pl-comparisons');
      this.el.innerHTML=`<section aria-label="Model comparison summary"><div class="pl-comparison-heading"><div><span class="pl-eyebrow">MODEL BENCHMARK</span><h2>Candidate comparisons</h2><p>OpenAI reference outputs × candidate models</p></div><span class="pl-comparison-count"></span></div><div class="pl-table-scroll"><table class="pl-model-table"><caption class="pl-sr-only">Candidate model summary</caption><thead><tr><th scope="col">Candidate / reference</th><th scope="col">Completed</th><th scope="col">Semantic agreement</th><th scope="col">Exact agreement</th><th scope="col">Median latency</th><th scope="col">Final answer tokens</th><th scope="col">Reasoning tokens</th><th scope="col">Failures</th><th scope="col">Skipped / waiting</th></tr></thead><tbody></tbody></table></div><p class="pl-metric-note">Semantic agreement uses supplied judge verdicts; uncertain and unjudged pairs are excluded. Exact agreement compares literal outputs. Neither is a correctness score. Latency uses completed pairs; tokens are means of reported usage. — means unknown.</p></section><section aria-label="Response comparisons"><div class="pl-comparison-heading"><div><span class="pl-eyebrow">OUTPUT REVIEW</span><h2>Response comparisons</h2></div><span class="pl-pair-count"></span></div><div class="pl-comparison-controls"><label>Candidate model<select aria-label="Filter candidate model"></select></label><label>Show<select aria-label="Filter response comparisons"><option value="completed">Completed</option><option value="different">Different outputs</option><option value="all">All statuses</option><option value="failed">Failures</option><option value="skipped">Skipped</option></select></label><label class="pl-comparison-search">Find request<input type="search" aria-label="Search response comparisons" placeholder="Request, ID, or output…"></label></div><div class="pl-table-scroll pl-pair-scroll"><table class="pl-pair-table"><caption class="pl-sr-only">Original and candidate outputs</caption><thead><tr><th scope="col">Request</th><th scope="col">OpenAI output</th><th scope="col">Candidate output</th><th scope="col">Agreement</th><th scope="col">Latency<br>OpenAI / candidate</th><th scope="col">Tokens<br>Answer / reasoning</th><th scope="col">Details</th></tr></thead><tbody></tbody></table></div><button data-more hidden>Show 100 more</button></section>`;
      const distributions=document.createElement('section');distributions.setAttribute('aria-label','Comparison distributions');
      distributions.innerHTML=`<div class="pl-comparison-heading"><div><span class="pl-eyebrow">DISTRIBUTIONS</span><h2>Latency &amp; token distributions</h2><p class="pl-distribution-cohort"></p></div><label class="pl-bin-control">Histogram bins<select aria-label="Histogram bins"><option value="6">6</option><option value="8" selected>8</option><option value="12">12</option></select></label></div><div class="pl-hist-grid"><div data-chart="latency"></div><div data-chart="reasoning"></div><div data-chart="nonReasoning"></div></div><p class="pl-metric-note">Latency uses completed pairs. Token charts show reported counters; originals are counted once. Non-reasoning output may include formatting or other channels. Click a legend to isolate models.</p>`;
      this.el.querySelector('[aria-label="Response comparisons"]').before(distributions);
      this.histograms={
        latency:new Histogram(distributions.querySelector('[data-chart="latency"]'),{title:'Latency',subtitle:'Original and candidate inference',unit:'ms'}),
        reasoning:new Histogram(distributions.querySelector('[data-chart="reasoning"]'),{title:'Reasoning tokens',subtitle:'Reported hidden reasoning',unit:'tokens'}),
        nonReasoning:new Histogram(distributions.querySelector('[data-chart="nonReasoning"]'),{title:'Non-reasoning output',subtitle:'Total output minus reasoning',unit:'tokens'})
      };
      this.listen('change',e=>{
        const model=e.target.matches('[aria-label="Filter candidate model"]'),filter=e.target.matches('[aria-label="Filter response comparisons"]');
        if(!model && !filter)return;
        if(model)this.model=e.target.value;if(filter)this.filter=e.target.value;
        this.limit=100;this.renderPairs();if(model)this.updateHistograms();
      });
      this.listen('input',e=>{if(e.target.matches('[aria-label="Search response comparisons"]')){this.query=e.target.value.toLowerCase();this.limit=100;this.renderPairs();}});
      this.listen('change',e=>{if(e.target.matches('[aria-label="Histogram bins"]'))Object.values(this.histograms).forEach(chart=>chart.setBins(Number(e.target.value)));});
      this.listen('click',e=>{
        const inspect=e.target.closest('[data-inspect]');if(inspect)this.options.onSelect?.({requestId:inspect.dataset.inspect,candidateId:inspect.dataset.candidate});
        if(e.target.closest('[data-more]')){this.limit+=100;this.renderPairs();}
      });
      this.update(options.requests || []);
    }
    update(requests) {
      if(this.destroyed)return;this.requests=requests.map(normalizeRequest);
      const groups=summarizeModels(this.requests), picker=this.el.querySelector('[aria-label="Filter candidate model"]');
      const models=[...new Set(this.requests.flatMap(r=>r.candidates.map(c=>c.model)))];
      if(!models.includes(this.model))this.model='';
      const modelSignature=JSON.stringify(models);
      if(this.modelSignature!==modelSignature){picker.innerHTML='<option value="">All candidate models</option>'+models.map(m=>`<option value="${esc(m)}">${esc(m.split('/').pop())}</option>`).join('');this.modelSignature=modelSignature;}
      picker.value=this.model;
      this.el.querySelector('.pl-comparison-count').textContent=`${groups.length} model ${groups.length===1?'pair':'pairs'} · ${this.requests.length} captures`;
      const average=(value,samples)=>value===null?'—':`<b>${value.toFixed(1)}</b><small>${samples} reported</small>`;
      this.el.querySelector('.pl-model-table tbody').innerHTML=groups.length?groups.map(g=>`<tr><th scope="row"><strong title="${esc(g.model)}">${esc(g.model.split('/').pop())}</strong><small>${esc(g.provider || 'Candidate')} · vs ${esc(g.reference.split('/').pop())}</small></th><td><b>${g.completed}</b><small>of ${g.total} captured</small></td><td>${g.semanticAgreement===null?'—':`<b>${(g.semanticAgreement*100).toFixed(1)}%</b>`}<small>${g.semanticMatches}/${g.semanticScored} judged · ${g.semanticUncertain} uncertain</small></td><td>${g.agreement===null?'—':`<b class="${g.agreement===1?'pl-green':'pl-orange'}">${(g.agreement*100).toFixed(1)}%</b><small>${g.matches}/${g.scored} outputs</small><span class="pl-agreement-meter" aria-hidden="true"><i style="width:${g.agreement*100}%"></i></span>`}</td><td><b>${ms(g.latencyMedian)}</b><small>OpenAI ${ms(g.referenceLatencyMedian)} · ${g.latencySamples} pairs</small></td><td>${average(g.answerMean,g.answerSamples)}<small>non-reasoning ${g.nonReasoningMean===null?'—':g.nonReasoningMean.toFixed(1)}</small></td><td>${average(g.reasoningMean,g.reasoningSamples)}</td><td><b class="${g.failed?'pl-error':''}">${g.failed}</b></td><td><b class="${g.skipped?'pl-orange':''}">${g.skipped}</b><small>${g.pending+g.running} waiting</small></td></tr>`).join(''):'<tr><td colspan="9" class="pl-empty">No candidate comparison jobs recorded for these captures.</td></tr>';
      this.renderPairs();this.updateHistograms();
    }
    updateHistograms() {
      for(const [metric,chart] of Object.entries(this.histograms))chart.update(comparisonDistributions(this.requests,metric,this.model));
      const completed=this.requests.reduce((n,r)=>n+r.candidates.filter(c=>r.original.status==='succeeded' && c.status==='succeeded' && (!this.model || c.model===this.model)).length,0);
      this.el.querySelector('.pl-distribution-cohort').textContent=`${completed} completed comparisons · sample counts shown per model`;
    }
    destroy() { Object.values(this.histograms).forEach(chart=>chart.destroy());super.destroy(); }
    renderPairs() {
      const pairs=[];
      for(const r of this.requests)for(const c of r.candidates){
        const agreement=outputAgreement(r,c);
        if(this.model && c.model!==this.model)continue;
        if(this.filter==='completed' && c.status!=='succeeded')continue;
        if(this.filter==='different' && (c.judgment ? c.judgment.status!=='completed' || c.judgment.verdict!=='different' : agreement!==false))continue;
        if(['failed','skipped'].includes(this.filter) && c.status!==this.filter)continue;
        if(this.query && !`${r.name} ${r.id} ${stringify(r.original.output)} ${stringify(c.output)}`.toLowerCase().includes(this.query))continue;
        pairs.push({r,c,agreement});
      }
      this.el.querySelector('.pl-pair-count').textContent=`${pairs.length} ${pairs.length===1?'comparison':'comparisons'}`;
      const output=item=>item.status==='succeeded' && item.output!==undefined?`<pre>${esc(stringify(item.output))}</pre>`:`<span class="pl-cell-reason">${esc(item.error || item.reason || LABELS[item.status])}</span>`;
      this.el.querySelector('.pl-pair-table tbody').innerHTML=pairs.length?pairs.slice(0,this.limit).map(({r,c,agreement})=>`<tr><th scope="row"><strong>${esc(r.name)}</strong><small>${esc(r.id.slice(-10))}</small></th><td>${output(r.original)}</td><td>${output(c)}<small title="${esc(c.model)}">${esc(c.model.split('/').pop())}</small></td><td>${c.judgment && c.status==='succeeded'?badge(c.judgment.status==='completed'?(c.judgment.verdict==='equivalent'?'succeeded':'mixed'):'skipped',evaluation(c)?.label || ({completed:c.judgment.verdict==='equivalent'?'Equivalent':'Different',uncertain:'Uncertain',pending:'Judging',running:'Judging'})[c.judgment.status] || 'Unjudged')+(known(c.judgment.confidence)?`<small>${(c.judgment.confidence*100).toFixed(0)}% confidence</small>`:''):agreement===null?badge(c.status,c.status==='succeeded'?'Unscored':undefined):badge(agreement?'succeeded':'mixed',agreement?'Exact match':'Text differs')}</td><td class="pl-number-cell">${ms(r.original.durationMs)}<small>${ms(c.durationMs)}</small></td><td class="pl-number-cell">${answerTokens(c)??'—'} / ${known(c.tokens?.reasoning)?c.tokens.reasoning:'—'}<small>non-reasoning ${known(c.tokens?.nonReasoning)?c.tokens.nonReasoning:'—'} · total ${known(c.tokens?.output)?c.tokens.output:'—'}</small></td><td><button data-inspect="${esc(r.id)}" data-candidate="${esc(c.id)}" aria-label="Inspect ${esc(r.name)}">Inspect ↗</button></td></tr>`).join(''):'<tr><td colspan="7" class="pl-empty">No comparisons match this view. Select All statuses to see waiting, failed, or skipped jobs.</td></tr>';
      this.el.querySelector('[data-more]').hidden=pairs.length<=this.limit;
    }
  }
  class RequestExplorer extends Component {
    constructor(el, options = {}) {
      super(el, options); this.requests = []; this.selectedId = null; this.query = ''; this.filter = 'all';
      this.el.innerHTML = `<div class="pl-section-title">Requests <span class="pl-count"></span></div><div class="pl-search"><label>Find request<input type="search" placeholder="Search requests…" aria-label="Search requests"></label><label class="pl-sr">Comparison status<select aria-label="Filter comparison status"><option value="all">All comparisons</option>${['completed','running','pending','skipped','failed','mixed','original'].map(s => `<option value="${s}">${LABELS[s]}</option>`).join('')}</select></label></div><div class="pl-request-list" role="list" aria-label="Requests"></div>`;
      this.list = this.el.querySelector('.pl-request-list');
      this.listen('input', e => { if (e.target.matches('input')) { this.query = e.target.value; this.renderList(); } });
      this.listen('change', e => { if (e.target.matches('select')) { this.filter = e.target.value; this.renderList(); } });
      this.listen('click', e => { const b = e.target.closest('[data-request]'); if (b) this.options.onSelect?.(b.dataset.request); });
      this.listen('keydown', e => { if (!e.target.matches('[data-request]')) return; const buttons = [...this.list.querySelectorAll('button')], i = buttons.indexOf(e.target); let next;
        if (e.key === 'ArrowDown') next = buttons[Math.min(i + 1, buttons.length - 1)];
        if (e.key === 'ArrowUp') next = buttons[Math.max(i - 1, 0)];
        if (e.key === 'Home') next = buttons[0]; if (e.key === 'End') next = buttons.at(-1);
        if (next) { e.preventDefault(); next.focus(); }
      });
    }
    update(requests, selectedId) { this.requests = requests; this.selectedId = selectedId; this.renderList(); }
    setFilter(filter) { if(filter!=='all' && !['completed','running','pending','skipped','failed','mixed','original'].includes(filter))throw new TypeError('Unknown request filter');this.filter=filter;this.el.querySelector('select').value=filter;this.renderList(); }
    renderList() {
      const visible = this.requests.filter(r => (this.filter === 'all' || comparisonStatus(r) === this.filter) && `${r.id} ${r.name} ${r.original.model} ${r.candidates.map(c => c.model).join(' ')}`.toLowerCase().includes(this.query.toLowerCase()));
      this.el.querySelector('.pl-count').textContent = `${visible.length}/${this.requests.length}`;
      this.list.innerHTML = visible.length ? visible.map(r => `<div role="listitem"><button class="pl-request ${r.id === this.selectedId ? 'is-selected' : ''}" data-request="${esc(r.id)}" aria-current="${r.id === this.selectedId ? 'true' : 'false'}"><span class="pl-request-name">${esc(r.name)}<span aria-hidden="true">›</span></span><span class="pl-request-meta"><span>${esc(r.id.slice(-10))}</span>${badge(comparisonStatus(r))}</span>${r.original.status === 'failed' ? '<small class="pl-error">Original failed</small>' : ''}</button></div>`).join('') : '<p class="pl-empty">No matching requests.</p>';
    }
  }
  class TraceMap extends Component {
    constructor(el, options = {}) {
      super(el, options); this.zoom = 1; this.request = null; this.candidateId = null; this.focusNode = null;
      this.el.innerHTML = `<div class="pl-section-title"><span>${icon('trace')} System trace<small>Original inference + independent comparisons</small></span><div class="pl-toolbar"><button data-zoom="-1" aria-label="Zoom out">−</button><output class="pl-zoom">100%</output><button data-zoom="1" aria-label="Zoom in">+</button><button data-fit>Fit</button></div></div><div class="pl-map-scroll" tabindex="0" aria-label="Trace map, scroll to pan"><div class="pl-map"></div></div><div class="pl-node-detail" role="status"></div>`;
      this.map = this.el.querySelector('.pl-map');
      this.listen('click', e => { const z = e.target.closest('[data-zoom]'); if (z) this.setZoom(this.zoom + Number(z.dataset.zoom) * .1); if (e.target.closest('[data-fit]')) this.fit();
        const n = e.target.closest('[data-node]'); if (n) { this.focusNode = n.dataset.node; this.renderDetail(); this.options.onNodeSelect?.(n.dataset.node); }
      });
    }
    setZoom(value) { this.zoom = Math.max(.5, Math.min(1.6, value)); this.map.style.zoom = this.zoom; this.el.querySelector('.pl-zoom').textContent = `${Math.round(this.zoom * 100)}%`; }
    fit() { const available = this.el.querySelector('.pl-map-scroll').clientWidth - 24; this.setZoom(Math.min(1, available / 690)); }
    update(request, candidateId) { this.request = request; this.candidateId = candidateId; this.render(); }
    node(id, name, sub, status, symbol) {
      return `<button class="pl-node pl-${esc(status)}" data-node="${id}"><span class="pl-window-bar"><b>▪ ▪</b><span>□</span></span><span class="pl-node-content">${icon(symbol)}<strong>${esc(name)}</strong><small>${esc(sub)}</small></span><span class="pl-node-status">${badge(status)}</span></button>`;
    }
    render() {
      const r = this.request;
      if (!r) { this.map.innerHTML = '<div class="pl-empty">Select a request to inspect its trace.</div>'; this.renderDetail(); return; }
      const c = r.candidates.find(x => x.id === this.candidateId) || r.candidates[0];
      const arrow = text => `<div class="pl-connector"><small>${esc(text)}</small><span>→</span></div>`;
      this.map.innerHTML = `<section class="pl-lane"><header><b class="pl-step">1</b><strong>Original inference</strong><span class="pl-lane-note">Returns to your application</span>${badge(r.original.status)}</header><div class="pl-node-row">${this.node('application', 'Application', r.name, 'succeeded', 'app')}${arrow('request')}${this.node('original', 'OpenAI / original', r.original.model, r.original.status, 'model')}${arrow(ms(r.original.durationMs))}${this.node('response', 'Original response', r.original.error || 'Provider result', r.original.status, 'response')}</div></section><div class="pl-branch"><span>┊</span><mark>Asynchronous capture</mark><span>↓</span></div><section class="pl-lane pl-comparison-lane"><header><b class="pl-step">2</b><strong>Comparison</strong><span class="pl-lane-note">Separate backend</span>${badge(comparisonStatus(r))}</header><div class="pl-node-row">${this.node('capture', 'Capture', r.captured ? 'Request + original response' : 'Not captured', r.captured ? 'succeeded' : 'skipped', 'capture')}${arrow('enqueue')}${this.node('queue', 'Gauge backend', c?.reason || 'Independent queue', !c ? 'skipped' : c.status === 'pending' ? 'pending' : c.status === 'skipped' ? 'skipped' : 'succeeded', 'queue')}${arrow(c ? ms(c.durationMs) : '—')}${this.node('candidate', c?.provider || 'Candidate', c?.model || 'No candidate configured', c?.status || 'skipped', 'model')}${arrow('evaluate')}${this.node('compare', 'Comparison', c?.status === 'succeeded' ? evaluation(c)?.label || 'Output available · ungraded' : 'No evaluation result', c?.status === 'succeeded' ? 'succeeded' : c?.status || 'skipped', 'compare')}</div></section>`;
      this.renderDetail();
    }
    renderDetail() {
      const details = { application: 'The application receives only the original provider response.', original: 'Original inference status is independent of all comparison jobs.', response: 'The response returned to the caller, unchanged by this viewer.', capture: 'Capture is disposable. Missing capture means missing evaluation.', queue: 'Queue saturation can skip comparison jobs without changing the original result.', candidate: 'Choose a candidate in the output inspector to inspect its trace.', compare: 'Grades are supplied by your backend. Matching text is not a correctness score.' };
      this.el.querySelector('.pl-node-detail').textContent = this.request ? details[this.focusNode] || 'Select any node for details. Scroll to pan · + / − to zoom.' : '';
    }
  }
  class OutputInspector extends Component {
    constructor(el, options = {}) {
      super(el, options); this.tab = 'context'; this.request = null; this.candidateId = null;
      this.listen('click', async e => { const tab = e.target.closest('[data-tab]'); if (tab) { this.tab = tab.dataset.tab; this.render(); this.el.querySelector(`[data-tab="${this.tab}"]`).focus(); }
        const copy = e.target.closest('[data-copy]'); if (copy) { try { const c = this.candidate(); const value = copy.dataset.copy === 'original' ? this.request.original.output : c?.output; await navigator.clipboard.writeText(stringify(value)); if (!this.destroyed) this.el.querySelector('.pl-copy-status').textContent = 'Copied output'; } catch { if (!this.destroyed) this.el.querySelector('.pl-copy-status').textContent = 'Clipboard unavailable. Select and copy the text below.'; } }
      });
      this.listen('change', e => { if (e.target.matches('[data-candidate]')) { this.candidateId = e.target.value; this.render(); this.options.onCandidateSelect?.(this.candidateId); } });
      this.listen('keydown', e => { if (!e.target.matches('[data-tab]') || !['ArrowRight','ArrowLeft','Home','End'].includes(e.key)) return; const tabs = [...this.el.querySelectorAll('[data-tab]')]; const i = tabs.indexOf(e.target); const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; e.preventDefault(); tabs[next].click(); });
    }
    candidate() { return this.request?.candidates.find(c => c.id === this.candidateId) || this.request?.candidates[0]; }
    update(request, candidateId) { if (request?.id !== this.request?.id) this.tab='context'; this.request = request; this.candidateId = candidateId; this.render(); }
    output(item, kind, title) {
      if (!item) return '<div class="pl-empty">No candidate configured.</div>';
      const token = item.tokens, answer = answerTokens(item);
      const hasOutput = item.status === 'succeeded' && item.output !== undefined;
      return `<section class="pl-output"><header><strong>${title}</strong>${hasOutput ? `<button data-copy="${kind}" aria-label="Copy ${title.toLowerCase()}">Copy</button>` : ''}</header><small class="pl-model">${esc(item.model)}</small>${hasOutput ? `<pre tabindex="0">${esc(stringify(item.output))}</pre>` : `<div class="pl-empty">${esc(item.error || item.reason || (item.status === 'succeeded' ? 'Output not captured' : `${LABELS[item.status]} · no output`))}</div>`}<div class="pl-output-meta">${badge(item.status)}<span>${ms(item.durationMs)}</span></div><dl class="pl-tokens"><div><dt>Input</dt><dd>${esc(token.input ?? '—')}</dd></div><div><dt>Final answer</dt><dd>${esc(answer ?? '—')}</dd></div><div><dt>Reasoning</dt><dd>${esc(token.reasoning ?? '—')}</dd></div><div><dt>Total output</dt><dd>${esc(token.output ?? '—')}</dd></div><div><dt>Non-reasoning</dt><dd>${esc(token.nonReasoning ?? '—')}</dd></div><div><dt>Unattributed</dt><dd>${esc(token.unattributed ?? '—')}</dd></div></dl></section>`;
    }
    render() {
      const r = this.request, c = this.candidate();
      if (!r) { this.el.innerHTML = '<div class="pl-empty">Select a request to inspect its context and outputs.</div>'; this.signature=''; return; }
      const signature=JSON.stringify([r,c?.id,this.tab]);
      if (signature===this.signature) return;
      this.signature=signature;
      this.el.innerHTML = `<div class="pl-section-title">Request & comparison</div><div class="pl-inspector-heading"><h2>${esc(r.name)}</h2><code>${esc(r.id)}</code>${badge(comparisonStatus(r))}</div><label class="pl-candidate-picker">Candidate<select data-candidate ${r.candidates.length ? '' : 'disabled'}>${r.candidates.length ? r.candidates.map(x => `<option value="${esc(x.id)}" ${x.id === c?.id ? 'selected' : ''}>${esc(x.model)} · ${LABELS[x.status]}</option>`).join('') : '<option>No candidate</option>'}</select></label><div class="pl-tabs" role="tablist" aria-label="Request details">${['context','outputs','diff','input','raw'].map(t => `<button role="tab" aria-selected="${this.tab === t}" tabindex="${this.tab === t ? 0 : -1}" data-tab="${t}">${t[0].toUpperCase()+t.slice(1)}</button>`).join('')}</div><div class="pl-tab-content" role="tabpanel" aria-label="${this.tab}"></div><div class="pl-copy-status pl-sr-only" role="status"></div>`;
      const content = this.el.querySelector('.pl-tab-content');
      if (this.tab === 'context') content.innerHTML=contextMarkup(r.input);
      if (this.tab === 'outputs') content.innerHTML = this.output(r.original, 'original', 'Original output') + this.output(c, 'candidate', 'Candidate output') + `<section class="pl-grade"><strong>Evaluation</strong>${c?.status === 'succeeded' ? `<p>${esc(evaluation(c)?.label || 'Ungraded')}</p><small>${esc(evaluation(c)?.explanation || 'No backend grade supplied.')}</small>` : '<p>No evaluation available.</p>'}</section>`;
      if (this.tab === 'input' || this.tab === 'raw') content.innerHTML = `<pre tabindex="0">${esc(stringify(this.tab === 'raw' ? r : r.input))}</pre>`;
      if (this.tab === 'diff') content.innerHTML = c?.status === 'succeeded' && r.original.status === 'succeeded' && c.output !== undefined && r.original.output !== undefined ? `<p class="pl-hint">Line comparison · original → candidate</p><div class="pl-diff">${diffLines(r.original.output, c.output).map(line => `<div class="${line.changed ? 'is-changed' : ''}"><pre>${esc(line.original)}</pre><pre>${esc(line.candidate)}</pre></div>`).join('')}</div>` : '<p class="pl-empty">Two captured successful outputs are needed for a diff.</p>';
    }
  }
  class Timeline extends Component {
    constructor(el, options = {}) {
      super(el, options); this.request = null; this.playhead = null; this.timer = null;
      this.listen('click', e => { if (e.target.closest('[data-replay]')) this.replay(); if (e.target.closest('[data-stop]')) this.stop(); const span = e.target.closest('[data-span]'); if (span) { const s = this.request?.spans.find(x => x.id === span.dataset.span); this.el.querySelector('.pl-span-detail').textContent = s ? `${s.name} · ${ms(s.startMs)} start · ${ms(s.durationMs)} duration · ${LABELS[s.status]}` : ''; this.options.onSpanSelect?.(s); } });
    }
    update(request) { this.stop(false); this.request = request; this.playhead = null; this.render(); }
    replay() { if (!this.request?.spans.length) return; this.stop(false); const start = performance.now(); this.playhead = 0; this.render(); this.timer = setInterval(() => { this.playhead = Math.min(1, (performance.now() - start) / 4000); this.paint(); if (this.playhead >= 1) this.stop(false); }, 40); }
    stop(render = true) { clearInterval(this.timer); this.timer = null; if (render) { this.playhead = null; this.render(); } }
    paint() { this.el.querySelector('.pl-timeline-rows')?.style.setProperty('--pl-progress', `${(this.playhead ?? 1) * 100}%`); const out = this.el.querySelector('.pl-replay-status'); if (out) out.textContent = this.playhead === null ? '' : this.playhead < 1 ? `Replay ${Math.round(this.playhead * 100)}%` : 'Replay complete'; }
    render() {
      const spans = this.request?.spans || [], max = Math.max(1, ...spans.map(s => s.startMs + s.durationMs));
      this.el.innerHTML = `<div class="pl-section-title"><span>Event timeline<small>Recorded spans for the selected request</small></span><div class="pl-toolbar"><span class="pl-replay-status" role="status"></span><button data-replay ${spans.length ? '' : 'disabled'}>▶ Replay</button><button data-stop ${spans.length ? '' : 'disabled'}>Reset</button></div></div>${spans.length ? `<div class="pl-time-axis"><span></span><div>${[0,.25,.5,.75,1].map(x => `<span>${ms(max*x)}</span>`).join('')}</div></div><div class="pl-timeline-rows">${spans.map(s => `<div class="pl-time-row"><span class="pl-span-name">${esc(s.name)}</span><div class="pl-time-track"><button data-span="${esc(s.id)}" class="pl-time-bar pl-${esc(s.status)} pl-kind-${['original','capture','queue','candidate','grade'].includes(s.kind) ? s.kind : 'capture'}" style="left:${s.startMs/max*100}%;width:${Math.max(.8,s.durationMs/max*100)}%;max-width:${100-s.startMs/max*100}%" aria-label="${esc(`${s.name}, ${ms(s.durationMs)}, ${LABELS[s.status]}`)}"></button></div></div>`).join('')}</div><div class="pl-span-detail" role="status">Select a span for timing details.</div>` : '<p class="pl-empty">No recorded spans. Timing is never synthesized from missing data.</p>'}`;
      this.paint();
    }
    destroy() { this.stop(false); super.destroy(); }
  }
  class Dashboard extends Component {
    constructor(el, options = {}) {
      super(el, options); this.requests = []; this.pending = new Map(); this.paused = false; this.selectedId = null; this.candidateId = null; this.running = false; this.events = new Map(); this.sourceCleanup = null; this.maxRequests = Math.max(1, Math.floor(finite(options.maxRequests, 1000)));
      this.el.classList.add('pl-dashboard');
      const comparisonLayout=options.layout==='comparisons';
      const traceMarkup='<section class="pl-trace" aria-label="Execution trace"></section>';
      const traceContent=comparisonLayout?`<details class="pl-trace-details"><summary>System trace</summary>${traceMarkup}</details>`:traceMarkup;
      const explorerMarkup='<aside class="pl-explorer" aria-label="Request explorer"></aside>';
      const inspectorMarkup='<section class="pl-inspector" aria-label="Request inspector"></section>';
      const timingMarkup=`<section class="pl-center">${traceContent}<section class="pl-timeline" aria-label="Event timeline"></section></section>`;
      let workspace=`<div class="pl-workspace">${explorerMarkup}${comparisonLayout?inspectorMarkup+timingMarkup:timingMarkup+inspectorMarkup}</div>`;
      if(comparisonLayout)workspace=`<div class="pl-comparison-content"><div class="pl-comparison-mount"></div><details class="pl-request-details"><summary>Request details · context, outputs, and timing</summary>${workspace}</details></div>`;
      this.el.innerHTML = `<header class="pl-header"><div class="pl-brand"><span class="pl-logo" aria-hidden="true">✣</span><strong>${esc(options.title || 'Gauge')}</strong><span class="pl-brand-tag">/ Pixel Lab</span></div><div class="pl-run-label">${esc(options.subtitle || 'Trace workspace')}</div><div class="pl-toolbar"><button data-action="pause">Ⅱ Pause updates</button><button data-action="export">↓ Export</button><button class="pl-primary" data-action="run" ${options.onRun ? '' : 'disabled'}>▶ Run test</button></div></header><div class="pl-summary" aria-label="Visible request summary"></div>${workspace}<footer class="pl-footer"><span><i class="pl-live-dot"></i> <span class="pl-connection">Local data</span></span><span class="pl-notice" role="status"></span><span>PIXEL LAB <b>v0.1</b></span></footer>`;
      this.explorer = new RequestExplorer(this.el.querySelector('.pl-explorer'), { onSelect: id => this.select(id) });
      this.trace = new TraceMap(this.el.querySelector('.pl-trace'), { onNodeSelect: id => this.emit('node:select', { id, request: this.selected() }) });
      this.inspector = new OutputInspector(this.el.querySelector('.pl-inspector'), { onCandidateSelect: id => { this.candidateId = id; this.trace.update(this.selected(), id); this.emit('candidate:select', { id, request: this.selected() }); } });
      this.timeline = new Timeline(this.el.querySelector('.pl-timeline'), { onSpanSelect: span => this.emit('span:select', span) });
      this.listen('click', e => { const action = e.target.closest('[data-action]')?.dataset.action; if (action === 'pause') this.setPaused(!this.paused); if (action === 'export') this.download(); if (action === 'run') this.run(); });
      this.requestDetails=this.el.querySelector('.pl-request-details');
      this.traceDetails=this.el.querySelector('.pl-trace-details');
      this.comparisons=comparisonLayout?new ComparisonSummary(this.el.querySelector('.pl-comparison-mount'),{onSelect:({requestId,candidateId})=>{
        this.select(requestId,candidateId);this.requestDetails.open=true;this.requestDetails.scrollIntoView({behavior:'auto',block:'start'});
      }}):null;
      this.setData(options.requests || []);
    }
    on(name, handler) { if (!this.events.has(name)) this.events.set(name, new Set()); this.events.get(name).add(handler); return () => this.events.get(name)?.delete(handler); }
    emit(name, detail) { for (const fn of this.events.get(name) || []) { try { fn(detail); } catch (error) { this.notice(`Event handler failed: ${error.message}`); } } }
    notice(text) { if (!this.destroyed) this.el.querySelector('.pl-notice').textContent = text; }
    selected() { return this.requests.find(r => r.id === this.selectedId) || null; }
    setData(values) {
      if (this.destroyed) return;
      const records = values.map(normalizeRequest); if (new Set(records.map(r => r.id)).size !== records.length) throw new TypeError('Request ids must be unique');
      this.requests = records.sort((a,b) => b.startedAt-a.startedAt).slice(0,this.maxRequests); this.pending.clear();
      if(this.requestDetails)this.requestDetails.open=false;if(this.traceDetails)this.traceDetails.open=false;
      if (!this.selected()) { this.selectedId = this.requests[0]?.id || null; this.candidateId = null; } this.render();
    }
    upsert(value) { this.upsertMany([value]); }
    upsertMany(values) {
      if(this.destroyed)return;const records=values.map(normalizeRequest);
      if(this.paused){for(const r of records){this.pending.delete(r.id);this.pending.set(r.id,r);}while(this.pending.size>this.maxRequests)this.pending.delete(this.pending.keys().next().value);this.notice(`${this.pending.size} buffered updates`);return;}
      this.apply(records);
    }
    apply(records) { const map = new Map(this.requests.map(r => [r.id,r])); records.forEach(r => map.set(r.id,r)); this.requests = [...map.values()].sort((a,b) => b.startedAt-a.startedAt).slice(0,this.maxRequests); if (!this.selected()) { this.selectedId = this.requests[0]?.id || null; this.candidateId = null; } this.render(); }
    select(id, candidateId=null) { const r=this.requests.find(r=>r.id===id);if(!r || (candidateId!==null && !r.candidates.some(c=>c.id===candidateId)))return false;this.selectedId=id;this.candidateId=candidateId;this.render();this.emit('request:select',this.selected());if(candidateId!==null)this.emit('candidate:select',{id:candidateId,request:this.selected()});return true; }
    setPaused(value) { this.paused = Boolean(value); this.el.querySelector('[data-action="pause"]').textContent = this.paused ? '▶ Resume updates' : 'Ⅱ Pause updates'; if (!this.paused) { const queued = [...this.pending.values()]; this.pending.clear(); this.apply(queued); } this.notice(this.paused ? 'Display paused · incoming updates are bounded and buffered' : 'Updates resumed'); }
    render() {
      const s = summarize(this.requests), r = this.selected(); if (!r?.candidates.some(c => c.id === this.candidateId)) this.candidateId = r?.candidates[0]?.id || null;
      this.el.querySelector('.pl-summary').innerHTML = `<span><b class="pl-green">${s.succeeded}/${s.total}</b> originals succeeded</span><span><b>${s.captured}</b> captures</span><span><b>${s.compared}</b> requests compared</span><span><b class="pl-orange">${s.skipped}</b> skipped</span>${s.failed ? `<span><b class="pl-error">${s.failed}</b> comparison errors</span>` : ''}<span class="pl-summary-note">${this.requests.length} most recent · comparisons are independent</span>`;
      this.explorer.update(this.requests, this.selectedId); this.trace.update(r, this.candidateId); this.inspector.update(r, this.candidateId); this.timeline.update(r);this.comparisons?.update(this.requests);
    }
    async run() {
      if (this.running || this.destroyed || !this.options.onRun) return; this.running = true; const button = this.el.querySelector('[data-action="run"]'); button.disabled = true; button.textContent = '… Running'; this.notice('Run requested');
      try { await this.options.onRun(this); if (!this.destroyed) this.notice('Run complete'); } catch (error) { this.notice(`Run failed: ${error.message}`); this.emit('run:error', error); } finally { this.running = false; if (!this.destroyed) { button.disabled = false; button.textContent = '▶ Run test'; } }
    }
    exportData() { return JSON.stringify({ version: 1, requests: this.requests }, null, 2); }
    download() { const url = URL.createObjectURL(new Blob([this.exportData()], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'pixel-lab-requests.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
    connect(subscribe) {
      this.disconnect(); let active = true;
      const sink = { upsertMany: values => { if(active && !this.destroyed){try{this.upsertMany(values);}catch(e){this.notice(`Invalid update: ${e.message}`);}} }, upsert: r => { if (active && !this.destroyed) { try { this.upsert(r); } catch(e) { this.notice(`Invalid update: ${e.message}`); } } }, status: text => { if (active && !this.destroyed) this.el.querySelector('.pl-connection').textContent = String(text); }, error: error => { if (active) this.notice(`Source error: ${error.message || error}`); } };
      let ownCleanup = null;
      try { const cleanup = subscribe(sink); if (cleanup != null && typeof cleanup !== 'function') throw new TypeError('subscribe must return a cleanup function or undefined'); ownCleanup = () => { if (!active) return; active = false; cleanup?.(); }; this.sourceCleanup = ownCleanup; } catch (e) { active = false; this.notice(`Connection failed: ${e.message}`); }
      return () => { if (ownCleanup && this.sourceCleanup === ownCleanup) this.disconnect(); };
    }
    disconnect() { const cleanup = this.sourceCleanup; this.sourceCleanup = null; try { cleanup?.(); } catch(e) { this.notice(`Disconnect failed: ${e.message}`); } }
    destroy() { this.disconnect(); [this.explorer,this.trace,this.inspector,this.timeline,this.comparisons].filter(Boolean).forEach(c => c.destroy()); this.events.clear(); this.pending.clear(); this.requests = []; this.el.classList.remove('pl-dashboard'); super.destroy(); }
  }
  return { Dashboard, ComparisonSummary, Histogram, binHistogram, comparisonDistributions, RequestExplorer, RequestContext, TraceMap, OutputInspector, Timeline, normalizeContext, normalizeRequest, normalizeUsage, summarize, summarizeModels, comparisonStatus, diffLines };
});
