/* Gauge wire schema -> Pixel Lab records. This file belongs to the Gauge app,
 * not the standalone visualization package. No provider credentials or calls. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.GaugeDashboard = api;
})(globalThis, function () {
  'use strict';
  const {normalizeUsage}=globalThis.PixelLab || require('../../pixel-lab/pixel-lab.js');
  const numeric = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
  const tokens=(usage,options)=>normalizeUsage(usage,options);
  function responseText(response) {
    if (!response) return undefined;
    if (typeof response.choices?.[0]?.message?.content === 'string') return response.choices[0].message.content;
    if (Array.isArray(response.output)) {
      const parts = response.output.flatMap(item => item.type === 'message' ? item.content || [] : []).filter(p => p.type === 'output_text' && typeof p.text === 'string');
      if (parts.length) return parts.map(p => p.text).join('\n');
    }
    return undefined;
  }
  function title(request, fallback) {
    const input = request.messages ?? request.input;
    const content = typeof input === 'string' ? input : Array.isArray(input) ? [...input].reverse().find(m => m.role === 'user')?.content : null;
    const text = typeof content === 'string' ? content : Array.isArray(content) ? content.map(p => p.text || '').join(' ') : '';
    return text.trim().slice(0,80) || fallback;
  }
  function textOnly(response){
    const message=response?.choices?.[0]?.message;
    if(message)return typeof message.content==='string' && !message.tool_calls?.length && !message.function_call && !message.audio;
    return Array.isArray(response?.output) && response.output.some(x=>x.type==='message') && response.output.every(x=>x.type==='reasoning' || (x.type==='message' && x.content?.every(p=>p.type==='output_text')));
  }
  function toRequest({capture: e, comparisons, comparison}) {
    if (!e || typeof e.call_id !== 'string') throw new TypeError('Invalid Gauge capture');
    const start = numeric(e.created_at) ?? 0, latency = numeric(e.latency_ms);
    const finish=e.response?.choices?.[0]?.finish_reason;
    const originalIncomplete=e.status==='completed' && ((finish!=null && finish!=='stop') || (e.response?.status!=null && e.response.status!=='completed'));
    const originalStatus = e.status === 'completed' ? (originalIncomplete?'skipped':'succeeded') : e.status === 'error' ? 'failed' : 'skipped';
    const originalText=responseText(e.response);
    const original = {model:e.request?.model || 'Unknown original model',status:originalStatus,output:originalText,durationMs:latency,tokens:tokens(e.response?.usage,{textOnly:textOnly(e.response),answerText:originalText}),
      error:e.error || undefined, reason:originalIncomplete?`Incomplete output (${finish || e.response.status})`:e.status === 'stream_passthrough' ? 'Stream passed through; final output was not captured' : e.status === 'cancelled' ? 'Caller cancelled the request' : undefined};
    const spans = [];
    if (latency !== null) spans.push({id:'original',name:'Original inference',kind:'original',startMs:0,durationMs:latency,status:originalStatus});
    const candidates = [];
    const jobs=comparisons ?? (comparison?[comparison]:[]);
    for (const job of jobs) {
      const incomplete=job.status==='completed' && job.finish_reason!=null && job.finish_reason!=='stop';
      const status = incomplete?'skipped':({completed:'succeeded',error:'failed',pending:'pending',running:'running',skipped:'skipped'})[job.status];
      if (!status) throw new TypeError('Invalid Gauge comparison status');
      const candidate = {id:job.candidate,provider:'Fireworks',model:job.candidate,status,reason:incomplete?`Incomplete output (${job.finish_reason}); excluded from scoring`:job.reason,error:job.error,
        durationMs:numeric(job.latency_ms),tokens:tokens(job.usage,{textOnly:status==='succeeded' && typeof job.candidate_output==='string',answerText:job.candidate_output})};
      if (status === 'succeeded') {
        candidate.output = job.candidate_output;
        const j=job.judgment;
        if(j){
          candidate.judgment=j;
          const label=({completed:j.verdict==='equivalent'?'Equivalent':'Different',uncertain:'Uncertain',pending:'Judging',running:'Judging',error:'Unjudged',skipped:'Unjudged'})[j.status] || 'Unjudged';
          const confidence=numeric(j.confidence);
          const compliance=j.answers?` Original compliance: ${j.answers.original_compliance?.verdict || 'unknown'}. Candidate compliance: ${j.answers.candidate_compliance?.verdict || 'unknown'}.`:'';
          candidate.grade={label,explanation:`${j.model || 'Judge'} · ${j.rubric_version || 'unknown rubric'}. ${confidence===null?'':`Confidence ${(confidence*100).toFixed(0)}%. `}${j.probabilities?`Probabilities: ${JSON.stringify(j.probabilities)}. `:''}${j.reason || j.error || 'Semantic comparison; not a correctness score.'}${compliance}`};
        }else if (typeof job.agreement === 'boolean') candidate.grade = {
          label:job.agreement ? 'Exact match' : 'Different output',
          explanation:'Literal output comparison against the original response; not a correctness score.'
        };
      }
      candidates.push(candidate);
      // Wall-clock offsets only for backend events; latency uses measured duration.
      // Clamped for clock skew. No invented capture/grader timing spans.
      const offset = t => Math.max(0, (t-start)*1000);
      const received = numeric(job.received_at), started = numeric(job.started_at), finished = numeric(job.finished_at);
      const queueEnd = started ?? finished;
      if (received !== null && queueEnd !== null) spans.push({id:'queue-'+job.candidate,name:'Backend queue',kind:'queue',startMs:offset(received),durationMs:Math.max(0,(queueEnd-received)*1000),status:status === 'skipped' ? 'skipped' : 'succeeded'});
      if (started !== null && finished !== null) spans.push({id:'candidate-'+job.candidate,name:job.candidate,kind:'candidate',startMs:offset(started),durationMs:Math.max(0,(finished-started)*1000),status});
    }
    return {id:e.call_id,name:title(e.request || {},e.call_id),startedAt:start*1000,captured:true,input:e.request,original,candidates,spans,
      metadata:{operation:e.operation,providerRequestId:e.provider_request_id,captureStatus:e.status,replayEligible:e.replay_eligible,ineligibleReason:e.ineligible_reason,
        comparisonHistory:jobs.length ? 'tracked' : 'unavailable; capture predates dashboard tracking',timing:'Backend offsets use wall clocks; original duration is measured.'}};
  }
  function pollingSource({intervalMs=1500,timeoutMs=5000,onSnapshot=()=>{},fetchImpl=globalThis.fetch.bind(globalThis),url='/v1/dashboard'} = {}) {
    return sink => {
      let stopped=false,timer=null,controller=null,failures=0;
      const seen=new Map();
      async function poll() {
        controller=new AbortController();const deadline=setTimeout(()=>controller?.abort(),timeoutMs);
        try {
          const options={credentials:'same-origin',cache:'no-store',signal:controller.signal};
          const response=await fetchImpl(url,options);
          if(!response.ok) throw new Error(`Dashboard HTTP ${response.status}`);
          const snapshot=await response.json();
          if(![1,2].includes(snapshot.schema_version) || !Array.isArray(snapshot.records)) throw new Error('Unsupported dashboard feed');
          let cursor=snapshot.next_cursor;
          while(cursor!=null && snapshot.records.length<1000){
            const separator=url.includes('?')?'&':'?';
            const next=await fetchImpl(`${url}${separator}before=${encodeURIComponent(cursor)}`,options);
            if(!next.ok)throw new Error(`Dashboard page HTTP ${next.status}`);
            const page=await next.json();
            if(page.schema_version!==snapshot.schema_version || !Array.isArray(page.records) || page.next_cursor===cursor)throw new Error('Invalid dashboard page');
            snapshot.records.push(...page.records);cursor=page.next_cursor;
          }
          snapshot.records=snapshot.records.slice(0,1000);
          snapshot.loaded=snapshot.records.length;
          // Validate the complete snapshot before changing the visible view.
          const records=snapshot.records.map(row=>({record:toRequest(row),version:JSON.stringify(row)}));
          if(stopped)return;
          const retained=new Set();
          const changed=[];
          for(const {record,version} of records) { retained.add(record.id);if(seen.get(record.id)!==version){changed.push(record);seen.set(record.id,version);} }
          if(changed.length){if(sink.upsertMany)sink.upsertMany(changed);else changed.forEach(record=>sink.upsert(record));}
          for(const id of seen.keys())if(!retained.has(id))seen.delete(id);
          failures=0;sink.status('Connected · polling');onSnapshot(snapshot);
        } catch(error) {
          if(!stopped){failures++;sink.status('Disconnected · retrying');sink.error(error);}
        } finally {
          clearTimeout(deadline);controller=null;
          if(!stopped)timer=setTimeout(poll,Math.min(15000,Math.max(250,intervalMs)*2**Math.min(failures,4)));
        }
      }
      sink.status('Connecting');poll();
      return ()=>{stopped=true;clearTimeout(timer);controller?.abort();seen.clear();};
    };
  }
  return {tokens,responseText,toRequest,pollingSource};
});
