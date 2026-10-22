const {chromium,expect,request}=require('../../../pixel-lab/node_modules/@playwright/test');
const {spawn,spawnSync}=require('node:child_process');
const {mkdtempSync,rmSync,mkdirSync}=require('node:fs');
const {tmpdir}=require('node:os');
const path=require('node:path');
const {once}=require('node:events');
const {pathToFileURL}=require('node:url');
(async()=>{
  const root=path.resolve(__dirname,'../../..'),temp=mkdtempSync(path.join(tmpdir(),'gauge-ui-'));
  let server,browser,api;
  async function start(){
    server=spawn(path.join(root,'gauge/.venv/bin/python'),[path.join(__dirname,'fixture_server.py'),path.join(temp,'inbox.sqlite')],{env:{...process.env,FIREWORKS_API_KEY:'unused-fixture-key',GAUGE_JUDGE:'off'},stdio:['ignore','pipe','pipe']});
    return await new Promise((resolve,reject)=>{
      let output='',errors='';const timer=setTimeout(()=>reject(new Error(`Startup timeout: ${errors}`)),10000);
      server.stderr.on('data',data=>errors+=data);
      server.on('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${errors}`));});
      server.stdout.on('data',data=>{output+=data;if(output.includes('\n')){clearTimeout(timer);resolve(JSON.parse(output.split('\n')[0]).port);}});
    });
  }
  async function stop(){if(server&&server.exitCode===null){server.kill();await once(server,'exit');}}
  const capture=(i,name='Dish soap')=>({schema_version:'1',call_id:`call_${i.toString(16).padStart(32,'0')}`,operation:'chat.completions.create',created_at:Date.now()/1000-.1,latency_ms:100,status:'completed',attempts:1,reference_source:'original_response',request:{model:'test/openai-reference',messages:[{role:'user',content:name}]},response:{choices:[{finish_reason:'stop',message:{content:'household'}}],usage:{prompt_tokens:10,completion_tokens:5,completion_tokens_details:{reasoning_tokens:0}}}});
  try{
    const port=await start(),url=`http://127.0.0.1:${port}`;
    api=await request.newContext({baseURL:url});
    browser=await chromium.launch({headless:true,executablePath:process.env.PIXEL_LAB_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
    page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE_ERROR',e.message);});
    page.on('console',m=>{if(m.type()==='error')console.error('BROWSER_CONSOLE',m.text());});
    page.on('requestfailed',r=>console.error('ASSET_FAILED',new URL(r.url()).pathname,r.failure()?.errorText));
    await page.goto(url+'/dashboard');
    await expect(page.locator('#app')).toBeVisible();
    await expect(page.locator('.pl-header')).toBeVisible();
    await expect(page.locator('.pl-connection')).toContainText('Connected');
    await expect(page.locator('#key')).toHaveCount(0);
    await page.reload();
    await expect(page.locator('.pl-connection')).toContainText('Connected');
    const run=(await (await api.post('/v1/runs/start',{data:{name:'Saved fixture run',models:['test/fireworks-candidate'],capacity:1,max_calls:8}})).json()).run;
    const performance={
      arms:Object.fromEntries(['native','gauge'].map(arm=>[arm,{calls:3,planned:3,successful:2,errors:1,incomplete:0,
        latencies_ms:[100,200],attempt_latencies_ms:[100,200,30000],p50_ms:150,p95_ms:195,p99_ms:199,throughput:2}])),
      delta:Object.fromEntries(['p50_ms','p95_ms','p99_ms','throughput'].map(k=>[k,{absolute:0,percent:0}])),
      matched_pairs:2,paired_mean_delta_ms:0,
      methodology:{model:'fixture',concurrency:2,batch_size:3,seed:42,calls_per_arm:3,description:'Fixture measurement method.'}
    };
    expect((await api.post('/v1/runs/progress',{data:{run_id:run.id,report:{performance}}})).status()).toBe(200);
    expect((await (await api.get('/v1/runs')).json()).runs[0].status).toBe('running');
    const first=capture(1);
    first.request.messages=[{role:'system',content:'Use the supplied catalogue.'},{role:'developer',content:'Return a category.'},{role:'user',content:'Look up dish soap.'},{role:'assistant',content:'Household cleaning supplies'},...first.request.messages];
    expect((await api.post('/v1/captures',{data:first,headers:{'Idempotency-Key':first.call_id}})).status()).toBe(202);
    await expect(page.locator('.pl-request')).toHaveCount(1,{timeout:7000});
    await page.getByRole('button',{name:'Inspect Dish soap',exact:true}).click();
    await expect(page.getByRole('tab',{name:'Context',exact:true})).toHaveAttribute('aria-selected','true');
    await expect(page.locator('.pl-context-entry')).toHaveCount(5);
    await expect(page.locator('.pl-context-entry[data-role=system]')).toContainText('Use the supplied catalogue.');
    await expect(page.locator('.pl-context-entry[data-role=assistant]')).toContainText('Household cleaning supplies');
    await page.getByRole('tab',{name:'Outputs',exact:true}).click();
    await expect(page.locator('.pl-output').nth(1)).toContainText('household',{timeout:7000});
    await expect(page.locator('.pl-tokens').nth(1)).toContainText('Reasoning30');
    await expect(page.locator('.pl-grade')).toContainText('Unjudged');
    await expect(page.getByRole('table',{name:'Candidate model summary'})).toBeVisible();
    await expect(page.getByRole('table',{name:'Original and candidate outputs'})).toBeVisible();
    await expect(page.getByLabel('Execution trace',{exact:true})).toBeHidden();
    await expect(page.locator('.pl-model-table tbody')).toContainText('100.0%');
    await expect(page.locator('.pl-pair-table tbody')).toContainText('household');
    const histogram=page.locator('[data-chart="latency"]');
    await expect(histogram.locator('svg')).toBeVisible();
    await expect(histogram.locator('.pl-hist-legend button')).toHaveCount(2);
    await histogram.locator('.pl-hist-bar').first().hover();
    await expect(histogram.locator('.pl-hist-tooltip')).toContainText('1 of 1 measurements');
    await histogram.getByRole('button',{name:/Original/}).click();
    await expect(histogram.getByRole('button',{name:/Original/})).toHaveAttribute('aria-pressed','false');
    await histogram.getByRole('button',{name:/Original/}).click();
    await page.getByLabel('Histogram bins',{exact:true}).selectOption('12');
    await expect(histogram.locator('svg')).toBeVisible();
    await page.getByRole('button',{name:'Inspect Dish soap',exact:true}).click();
    await expect(page.getByLabel('Request inspector',{exact:true})).toBeVisible();
    await expect(page.getByLabel('Execution trace',{exact:true})).toBeHidden();
    await api.post('/v1/captures',{data:first,headers:{'Idempotency-Key':first.call_id}});
    expect((await (await api.get('/v1/dashboard')).json()).records).toHaveLength(1);
    const failed=capture(2,'Provider failure');
    await api.post('/v1/captures',{data:failed,headers:{'Idempotency-Key':failed.call_id}});
    await expect(page.locator('.pl-request')).toHaveCount(2,{timeout:7000});
    await page.locator(`[data-request="${failed.call_id}"]`).click();
    await page.getByRole('tab',{name:'Outputs',exact:true}).click();
    await expect(page.locator('.pl-output').nth(1)).toContainText('RuntimeError',{timeout:7000});
    await expect(page.locator('.pl-output').first()).toContainText('Succeeded');
    await api.post('/control',{data:{paused:true}});
    for(const i of [3,4,5]){
      const item=capture(i,`Queue test ${i}`);await api.post('/v1/captures',{data:item,headers:{'Idempotency-Key':item.call_id}});
      if(i===3)await expect.poll(async()=> (await (await api.get('/stats')).json()).active).toBe(1);
    }
    await expect(page.locator('.pl-request')).toHaveCount(5,{timeout:7000});
    await page.locator(`[data-request="${capture(5).call_id}"]`).click();
    await page.getByRole('tab',{name:'Outputs',exact:true}).click();
    await expect(page.locator('.pl-output').nth(1)).toContainText('Comparison queue full');
    await expect(page.locator('.pl-output').nth(1).locator('pre')).toHaveCount(0);
    await expect(page.locator('#server-state')).toContainText('dispatch paused');
    await expect(page.getByRole('region',{name:'Live comparison queue'})).toBeVisible();
    await expect(page.locator('#queue-queued')).toHaveText('1');
    await expect(page.locator('#queue-active')).toHaveText('1');
    await expect(page.locator('#queue-dropped')).toHaveText('1');
    await expect(page.locator('#queue-note')).toContainText('Dispatch paused');
    expect((await api.post('/v1/runs/end',{data:{run_id:run.id,report:{performance,checks:{passed:true},baseline:{calls:0},during_saturation:{calls:5}}}})).status()).toBe(200);
    await expect.poll(async()=> (await (await api.get('/stats')).json()).active).toBe(0);
    await page.goto(url+'/dashboard');
    await expect(page.locator('#server-state')).toContainText('5/5 saved captures',{timeout:15000});
    await expect(page.locator('#run')).toHaveValue(run.id);
    await expect(page.locator('#queue-title')).toHaveText('SAVED COMPARISON JOBS');
    const ab=page.locator('#performance-summary');
    await expect(ab).toBeVisible();
    await expect(ab.getByRole('table')).toContainText('A · Native OpenAI');
    await expect(ab.locator('.pl-hist-overlay')).toBeVisible();
    const coordinates=await ab.locator('.pl-hist-bar').evaluateAll(bars=>bars.map(b=>b.getAttribute('x')));
    expect(new Set(coordinates).size).toBeLessThan(coordinates.length);
    await ab.getByLabel('Performance latency population').selectOption('all');
    await expect(ab.locator('.pl-hist-heading')).toContainText('6 measurements');
    await ab.getByRole('button',{name:/A · Native OpenAI/}).click();
    await expect(ab.locator('.pl-hist-heading')).toContainText('3 measurements');
    await ab.getByRole('button',{name:/A · Native OpenAI/}).click();
    await ab.getByLabel('Performance histogram bins').selectOption('12');
    await ab.getByLabel('Performance latency population').selectOption('success');
    await expect(ab.locator('.pl-hist-heading')).toContainText('4 measurements');

    await expect(page.locator('#queue-completed')).toHaveText('3');
    await expect(page.locator('#queue-failed')).toHaveText('1');
    await page.reload();
    await expect(page.locator('#server-state')).toContainText('5/5 saved captures',{timeout:15000});
    await expect(page.locator('#key')).toHaveCount(0);
    await page.locator('.pl-request-details > summary').click();
    await page.getByLabel('Filter comparison status').selectOption('all');
    await page.locator(`[data-request="${first.call_id}"]`).click();
    mkdirSync(path.join(temp,'screenshots'),{recursive:true});
    await page.screenshot({path:path.join(temp,'screenshots/connected.png'),fullPage:true});
    await page.getByRole('button',{name:'Disconnect',exact:true}).click();
    await expect(page.locator('.pl-request')).toHaveCount(0);
    await expect(page.getByRole('heading',{name:'Dashboard disconnected'})).toBeVisible();
    await expect(page.locator('#app')).toHaveAttribute('data-loading','true');
    await expect(page.locator('.pl-header')).toBeVisible();
    // Restart the actual HTTP service against the same durable database.
    await stop();const newPort=await start();await api.dispose();
    api=await request.newContext({baseURL:`http://127.0.0.1:${newPort}`});
    const recovered=(await (await api.get('/v1/dashboard')).json()).records;
    expect(recovered.find(r=>r.capture.call_id===first.call_id).comparisons[0].status).toBe('completed');
    expect(recovered.find(r=>r.capture.call_id===capture(3).call_id).comparisons[0].status).toBe('completed');
    await page.goto(`http://127.0.0.1:${newPort}/dashboard`);
    await expect(page.locator('#server-state')).toContainText('5/5 saved captures',{timeout:15000});
    await expect(page.locator('#run')).toHaveValue(run.id);
    await expect(page.locator('#performance-summary')).toContainText('Same calls. Two clients.');
    // Offline HTML uses the same library and must work without a backend.
    const reportPath=path.join(temp,'offline.html');
    const rendered=spawnSync(path.join(root,'gauge/.venv/bin/python'),['-m','gauge','demo','--output',reportPath],{encoding:'utf8'});
    expect(rendered.status,rendered.stderr).toBe(0);
    const offline=await browser.newPage();const offlineErrors=[];
    offline.on('pageerror',error=>offlineErrors.push(error.message));
    await offline.goto(pathToFileURL(reportPath).href);
    await expect(offline.getByRole('table',{name:'Candidate model summary'})).toBeVisible();
    await expect(offline.locator('.pl-model-table tbody tr')).toHaveCount(2);
    await expect(offline.locator('.pl-histogram svg')).toHaveCount(3);
    await expect(offline.locator('.pl-pair-count')).toHaveText('15 comparisons');
    await offline.getByLabel('Filter response comparisons').selectOption('different');
    await expect(offline.locator('.pl-pair-count')).toHaveText('2 comparisons');
    await offline.locator('[data-inspect]').first().click();
    await expect(offline.getByLabel('Request inspector',{exact:true})).toBeVisible();
    expect(offlineErrors).toEqual([]);await offline.close();
    expect((await (await api.get('/stats')).json()).results).toBeUndefined();
    // Run cleanup is previewed and backed up; this database is a disposable fixture.
    expect((await page.request.post(`http://127.0.0.1:${newPort}/v1/runs/clear/preview`,{data:{run_ids:[run.id]}})).status()).toBe(200);
    const preview=(await (await api.post('/v1/runs/clear/preview',{data:{run_ids:[run.id]}})).json());
    expect(preview.captures).toBe(5);expect(preview.comparisons).toBe(5);
    const cleared=await api.post('/v1/runs/clear',{data:{run_ids:preview.run_ids,fingerprint:preview.fingerprint}});
    expect(cleared.status()).toBe(200);expect((await cleared.json()).backup).toContain('gauge-before-clear');
    expect((await (await api.get('/v1/runs')).json()).runs).toHaveLength(0);
    expect((await (await api.get('/v1/dashboard')).json()).total).toBe(0);
    expect(errors).toEqual([]);
    console.log('Gauge browser integration passed: credential-free feed, real HTTP ingestion, correlated outputs/tokens, failures, queue saturation, disconnect, restart persistence. Provider was a test fixture; no inference calls.');
  }finally{await browser?.close();await api?.dispose();await stop();rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
