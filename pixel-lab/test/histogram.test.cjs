const {test}=require('node:test');
const assert=require('node:assert/strict');
const {binHistogram,comparisonDistributions,normalizeRequest}=require('../pixel-lab.js');
test('shared bins conserve observations including zero and the maximum; missing values stay missing',()=>{
  const data=binHistogram([{id:'a',values:[0,1,2,10,null,NaN,-1]},{id:'b',values:[10,10]}],4);
  assert.equal(data.max,10);assert.deepEqual(data.series[0].counts,[3,0,0,1]);
  assert.deepEqual(data.series[1].counts,[0,0,0,2]);assert.equal(data.series[0].excluded,3);
  assert.equal(data.series[0].median,1.5);assert.ok(Math.abs(data.series[0].p95-8.8)<1e-9);
});
test('empty, constant, and outlier-heavy measurements retain a valid domain and all samples',()=>{
  for(const values of [[],[0,0],[12,12,12],[.1,.2],[1,2,1e7]]){
    const data=binHistogram([{id:'a',values}],8);
    assert.ok(data.max>0 && Number.isFinite(data.max));assert.equal(data.series[0].counts.reduce((a,b)=>a+b,0),values.length);
    assert.ok(data.bins.every(b=>b.end>b.start));
  }
  assert.throws(()=>binHistogram([{id:'a',values:[]},{id:'a',values:[]}]),/unique/);
  assert.throws(()=>binHistogram([],0),/bins/);
});
test('comparison cohorts exclude failures and skips; originals are deduplicated across models',()=>{
  const original={model:'reference',status:'succeeded',durationMs:100,tokens:{reasoning:0}};
  const qwen={id:'qwen',model:'qwen',status:'succeeded',durationMs:50,tokens:{reasoning:5}};
  const rows=[normalizeRequest({id:'a',original,candidates:[qwen,{...qwen,id:'oss',model:'oss',durationMs:75}]}),normalizeRequest({id:'b',original,candidates:[{...qwen,status:'skipped'}]}),normalizeRequest({id:'c',original:{...original,status:'failed'},candidates:[qwen]})];
  const series=comparisonDistributions(rows,'latency');
  assert.equal(series.length,3);assert.deepEqual(series.map(s=>s.values),[[100],[50],[75]]);
  assert.equal(comparisonDistributions(rows,'latency','oss').length,2);
  const unknown=normalizeRequest({id:'d',original,candidates:[{...qwen,tokens:{reasoning:null}}]});
  const tokenSeries=binHistogram(comparisonDistributions([unknown],'reasoning'));
  assert.equal(tokenSeries.series[0].values.length,1);assert.equal(tokenSeries.series[1].values.length,0);
});
