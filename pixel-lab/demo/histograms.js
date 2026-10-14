/* Seeded fixtures for the standalone Histogram component. No network requests. */
(function () {
  'use strict';
  const scenario = document.querySelector('#demo-scenario');
  const state = document.querySelector('#demo-sample-state');
  let seed = 42;
  const charts = {
    latency: new PixelLab.Histogram('#demo-latency', {title:'Inference latency', unit:'ms'}),
    reasoning: new PixelLab.Histogram('#demo-reasoning', {title:'Reasoning tokens', unit:'tokens'}),
    coverage: new PixelLab.Histogram('#demo-coverage', {title:'Reported output tokens', unit:'tokens'}),
    tail: new PixelLab.Histogram('#demo-tail', {title:'Latency with a long tail', unit:'ms'}),
    empty: new PixelLab.Histogram('#demo-empty', {title:'Final answer tokens', unit:'tokens'})
  };
  function randomFrom(value) {
    return () => {
      value = (Math.imul(value, 1664525) + 1013904223) >>> 0;
      return value / 4294967296;
    };
  }
  function render() {
    const random = randomFrom(seed);
    const count = scenario.value === 'small' ? 8 : 192;
    const tailRate = scenario.value === 'tail' ? .14 : .035;
    const bell = (center, spread) => Math.max(0, center + (random()+random()+random()+random()-2)*spread);
    const values = factory => Array.from({length:count}, (_,index) => factory(index));
    const original = values(() => Math.round(bell(680, 280)));
    const qwen = values(() => Math.round(bell(430, 170)));
    const oss = values(() => Math.round(bell(520, 230)));
    charts.latency.update([
      {id:'original', label:'Original', color:'blue', values:original},
      {id:'qwen', label:'Qwen / candidate A', color:'purple', values:qwen},
      {id:'oss', label:'GPT-OSS / candidate B', color:'mint', values:oss}
    ]);
    charts.reasoning.update([
      {id:'original', label:'Original · observed zero', color:'blue', values:values(() => 0)},
      {id:'qwen', label:'Qwen / candidate A', color:'purple', values:values(() => Math.round(bell(360, 220)))},
      {id:'oss', label:'GPT-OSS / candidate B', color:'mint', values:values(() => Math.round(bell(640, 340)))}
    ]);
    const reported = values(index => index % 4 === 0 ? null : Math.round(bell(42, 22)));
    charts.coverage.update([
      {id:'original', label:'Original · usage unavailable', color:'blue', values:values(() => null)},
      {id:'qwen', label:'Qwen · partially reported', color:'purple', values:reported}
    ]);
    charts.tail.update([
      {id:'steady', label:'Steady latency', color:'blue', values:original},
      {id:'tail', label:'Occasional slow calls', color:'orange', values:values(() => Math.round(random()<tailRate ? bell(7400, 1200) : bell(450, 200)))}
    ]);
    charts.empty.update([{id:'unreported', label:'No final-answer counter supplied', color:'purple', values:[]}]);
    state.textContent = `${count} synthetic calls per arm · seed ${seed} · ${reported.filter(v=>v!==null).length}/${count} output counters reported`;
  }
  scenario.addEventListener('change', render);
  document.querySelector('#demo-bins').addEventListener('change', event => {
    Object.values(charts).forEach(chart => chart.setBins(Number(event.target.value)));
  });
  document.querySelector('#demo-resample').addEventListener('click', () => { seed++; render(); });
  render();
  window.addEventListener('pagehide', () => Object.values(charts).forEach(chart => chart.destroy()), {once:true});
})();
