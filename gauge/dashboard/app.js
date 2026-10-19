(function () {
  'use strict';
  const form=document.querySelector('#connect-form'),disconnect=document.querySelector('#disconnect'),state=document.querySelector('#server-state'),picker=document.querySelector('#run');
  const view=new PixelLab.Dashboard('#app',{title:'Gauge',subtitle:'BACKEND / saved runs + live captures',maxRequests:1000,layout:'comparisons'});
  let projectId=new URLSearchParams(window.location.search).get('project_id') || '';
  const projectPicker=document.querySelector('#project');
  const scoped=path=>path+(projectId?(path.includes('?')?'&':'?')+'project_id='+encodeURIComponent(projectId):'');
  let runs=[],active=false,generation=0,lastLiveRunId=null;
  const queuePanel=document.querySelector('#queue-status');
  const validationView=new GaugeValidationSummary(document.querySelector('#validation-summary'));
  const app=document.querySelector('#app'),panel=document.querySelector('#load-state');
  // Keep the library header and its delegated controls at the top of the page.
  view.el.querySelector('.pl-header').after(document.querySelector('.connection'),queuePanel,document.querySelector('#validation-summary'),panel);
  app.hidden=false;
  function showState(title,message){
    queuePanel.hidden=true;validationView.update(null);
    app.dataset.loading='true';panel.hidden=false;picker.disabled=true;
    document.querySelector('#load-title').textContent=title;
    document.querySelector('#load-message').textContent=message;
    state.textContent=message;
  }
  const headers=()=>({});
  async function loadRuns(){
    const response=await fetch(scoped('/v1/runs'),{headers:headers(),credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(5000)});
    if(!response.ok){const error=new Error(`Backend returned HTTP ${response.status}.`);error.unauthorized=response.status===401;throw error;}
    return (await response.json()).runs;
  }
  function showRuns(items,selected){
    runs=items;picker.replaceChildren(new Option('Live / all captures',''));
    for(const run of runs)picker.add(new Option(`${run.name} · ${run.status}`,run.id));
    picker.value=selected;
  }
  function connect(runId){
    const current=++generation;let initialized=false;
    view.disconnect();view.setPaused(false);view.setData([]);
    view.explorer.setFilter('all');
    active=true;disconnect.disabled=false;form.classList.add('session-connected');
    showState('Loading saved results…','Fetching captures and comparisons from the backend.');
    const location=new URL(window.location.href);
    if(projectId)location.searchParams.set('project_id',projectId);else location.searchParams.delete('project_id');
    if(runId)location.searchParams.set('run',runId);else location.searchParams.delete('run');
    history.replaceState(null,'',location);
    const source=GaugeDashboard.pollingSource({url:scoped('/v1/dashboard'+(runId?'?run_id='+encodeURIComponent(runId):'')),onSnapshot:snapshot=>{
      if(current!==generation)return;
      app.dataset.loading='false';panel.hidden=true;picker.disabled=false;
      const selected=runs.find(r=>r.id===runId),s=snapshot.stats,summary=selected?.summary;
      validationView.update(summary?.validation);
      if(s.active_run_id)lastLiveRunId=s.active_run_id;
      const currentRun=!runId || runId===s.active_run_id || (!s.active_run_id && runId===lastLiveRunId);
      queuePanel.hidden=false;queuePanel.dataset.live=String(currentRun);
      {
        const jobs=snapshot.records.flatMap(row=>row.comparisons ?? (row.comparison?[row.comparison]:[]));
        const saved={received:snapshot.loaded,comparison_jobs:jobs.length,workers:selected?.workers||1,
          queued:jobs.filter(j=>j.status==='pending').length,active:jobs.filter(j=>j.status==='running').length,
          completed:jobs.filter(j=>j.status==='completed').length,failed:jobs.filter(j=>j.status==='error').length,
          queue_dropped:jobs.filter(j=>j.status==='skipped' && j.reason==='Comparison queue full').length};
        const metrics=currentRun?s:saved;
        const total=metrics.comparison_jobs ?? metrics.received,finished=currentRun
          ?s.completed+s.failed+s.queue_dropped+s.ineligible*(s.models?.length||1)
          :saved.completed+saved.failed+jobs.filter(j=>j.status==='skipped').length;
        for(const metric of ['queued','active','completed','failed'])document.querySelector('#queue-'+metric).textContent=Number(metrics[metric]||0).toLocaleString();
        document.querySelector('#queue-dropped').textContent=Number(metrics.queue_dropped||0).toLocaleString();
        const progress=document.querySelector('#queue-progress');progress.max=Math.max(1,total);progress.value=finished;
        document.querySelector('#queue-title').textContent=currentRun?'COMPARISON QUEUE':'SAVED COMPARISON JOBS';
        document.querySelector('#queue-updated').textContent=`${metrics.workers||1} workers · updated ${new Date().toLocaleTimeString()} · ${currentRun?'polls ~1.5s':'saved run'}`;
        document.querySelector('#queue-note').textContent=`${finished.toLocaleString()} / ${total.toLocaleString()} jobs finished · ${metrics.received.toLocaleString()} captured requests · `+(currentRun
          ?`${s.paused?'Dispatch paused':'Dispatch enabled'} · queued jobs wait on the backend; OpenAI calls do not wait for them.`
          :'Persisted job states for this run; live activity from other runs is excluded.');
      }
      const judged=snapshot.records.flatMap(row=>row.comparisons || []).map(j=>j.judgment).filter(Boolean);
      const judgeCounts=status=>judged.filter(j=>j.status===status).length;
      if(s.configuration_note)document.querySelector('#queue-note').textContent+=' '+s.configuration_note;
      document.querySelector('#queue-note').textContent+=` Judging: ${judgeCounts('pending')} queued · ${judgeCounts('running')} active · ${judgeCounts('completed')} decided · ${judgeCounts('uncertain')} uncertain · ${judgeCounts('error')} errors · ${judgeCounts('skipped')} skipped.`;
      const baseline=summary?.baseline?.calls||0,captured=summary?.during_saturation?.calls||0;
      state.textContent=selected
        ? `${selected.status} · ${snapshot.loaded}/${snapshot.total} saved captures${summary?` · ${baseline+captured} OpenAI calls (${baseline} native baseline)` : ''}`
        : `Queue ${s.queued} · active ${s.active} · ${s.paused?'dispatch paused':'dispatch enabled'} · ${snapshot.loaded}/${snapshot.total} captures`;
      if(!initialized && selected?.status==='completed'){
        const completed=view.requests.find(r=>r.candidates.some(c=>c.status==='succeeded'));
        if(completed){view.explorer.setFilter('all');view.select(completed.id);}
      }
      if(!initialized && snapshot.loaded && view.requests.every(r=>!r.candidates.length))view.requestDetails.open=true;
      initialized=true;
      loadRuns().then(items=>{if(current===generation)showRuns(items,runId);}).catch(()=>{});
    }});
    view.connect(sink=>source({...sink,error:error=>{
      if(current!==generation)return;
      sink.error(error);
      showState('Results could not be loaded',`${error.message} Retrying automatically.`);
    }}));
  }
  async function connectBackend(){
    const current=++generation;
    view.disconnect();active=false;disconnect.disabled=true;
    showState('Loading saved results…','Connecting to the backend. Counts will appear after results load.');
    try{
      const projectsResponse=await fetch('/v1/projects',{cache:'no-store',signal:AbortSignal.timeout(5000)});
      if(!projectsResponse.ok)throw new Error('Could not load projects');
      const projects=(await projectsResponse.json()).projects;
      if(projects.length){
        if(projectId && !projects.some(p=>p.id===projectId))throw new Error('Selected project was not found on this backend.');
        if(!projectId)projectId=projects[0].id;
        projectPicker.replaceChildren(...projects.map(p=>new Option(p.name,p.id)));
        projectPicker.value=projectId;projectPicker.disabled=false;
      }
      const items=await loadRuns();if(current!==generation)return;
      const requested=new URLSearchParams(window.location.search).get('run');
      const selected=items.some(r=>r.id===requested)?requested:items[0]?.id||'';
      showRuns(items,selected);connect(selected);
    }catch(error){if(current===generation){
      form.classList.remove('session-connected');
      showState('Backend connection failed',`${error.message} Retry when the backend is available.`);
      view.el.querySelector('.pl-connection').textContent='Disconnected';
    }}
  }
  document.querySelector('#retry').addEventListener('click',connectBackend);
  form.addEventListener('submit',event=>{event.preventDefault();connectBackend();});
  picker.addEventListener('change',()=>connect(picker.value));
  projectPicker.addEventListener('change',()=>{projectId=projectPicker.value;connectBackend();});
  disconnect.addEventListener('click',()=>{generation++;active=false;view.disconnect();view.setData([]);;form.classList.remove('session-connected');view.notice('Disconnected');view.el.querySelector('.pl-connection').textContent='Disconnected';showState('Dashboard disconnected','Saved runs remain on the backend. Reconnect to load them.');disconnect.disabled=true;});
  window.addEventListener('pagehide',()=>{generation++;if(active)view.disconnect();view.destroy();},{once:true});
  connectBackend();
})();
