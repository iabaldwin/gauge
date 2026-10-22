/* Caller performance belongs to Gauge; histogram rendering stays in Pixel Lab. */
(function (root) {
  'use strict';
  const number = (value, digits=1) => value == null ? '—' : value.toLocaleString(undefined, {maximumFractionDigits:digits});
  const signed = value => value == null ? '—' : `${value > 0 ? '+' : ''}${number(value)}`;
  class PerformanceSummary {
    constructor(el) {
      this.el = el;
      this.signature = '';
      this.histogram = null;
      this.data = null;
      this.allAttempts = false;
    }
    update(data) {
      const signature = JSON.stringify(data || null);
      if (signature === this.signature) return;
      this.signature = signature;
      this.data = data;
      this.el.hidden = !data;
      if (!data) { this.destroy(); this.el.replaceChildren(); return; }
      if (!this.histogram) this.mount();
      const {arms, delta, methodology:m} = data;
      this.meta.textContent = `${m.model} · concurrency ${m.concurrency} · ${m.calls_per_arm.toLocaleString()} calls per arm · randomized batches of ${m.batch_size} · seed ${m.seed}`;
      this.rows.replaceChildren();
      const row = (label, a, b, difference='—') => {
        const tr = this.add('tr', '', this.rows);
        for (const text of [label,a,b,difference.replace(' (', '\n(')]) this.add('td', text, tr);
      };
      row('Calls measured', `${arms.native.calls} / ${arms.native.planned}`, `${arms.gauge.calls} / ${arms.gauge.planned}`);
      for (const [label, key] of [['p50 latency','p50_ms'],['p95 latency','p95_ms'],['p99 latency','p99_ms']]) {
        row(label, `${number(arms.native[key])} ms`, `${number(arms.gauge[key])} ms`,
          `${signed(delta[key].absolute)} ms (${signed(delta[key].percent)}%)`);
      }
      row('Active-batch throughput', `${number(arms.native.throughput,2)} calls/s`, `${number(arms.gauge.throughput,2)} calls/s`,
        `${signed(delta.throughput.absolute)} calls/s (${signed(delta.throughput.percent)}%)`);
      row('Errors / incomplete', `${arms.native.errors} / ${arms.native.incomplete}`, `${arms.gauge.errors} / ${arms.gauge.incomplete}`);
      this.pairs.textContent = `${data.matched_pairs.toLocaleString()} successful matched pairs · mean Gauge − native latency: ${signed(data.paired_mean_delta_ms)} ms. Positive latency differences mean Gauge was slower.`;
      this.method.textContent = m.description;
      this.renderHistogram();
    }
    add(tag, text, parent=this.el) {
      const node = document.createElement(tag);
      node.textContent = text;
      parent.append(node);
      return node;
    }
    mount() {
      this.el.replaceChildren();
      this.add('span', 'CALLER PERFORMANCE / A × B').className = 'pl-eyebrow';
      this.add('h2', 'Same calls. Two clients.');
      this.meta = this.add('p', '');
      const controls = this.add('div', '');
      controls.className = 'performance-controls';
      const label = this.add('label', 'Latency distribution ', controls);
      const select = this.add('select', '', label);
      select.setAttribute('aria-label', 'Performance latency population');
      select.append(new Option('Successful complete outputs', 'success'), new Option('All attempts, including errors', 'all'));
      select.value = this.allAttempts ? 'all' : 'success';
      select.addEventListener('change', () => { this.allAttempts = select.value === 'all'; this.renderHistogram(); });
      const binLabel = this.add('label', 'Bins ', controls);
      const bins = this.add('select', '', binLabel);
      bins.setAttribute('aria-label', 'Performance histogram bins');
      for (const value of [12,24,32]) bins.append(new Option(String(value),String(value)));
      bins.value = '24';
      bins.addEventListener('change', () => this.histogram.setBins(Number(bins.value)));
      const grid = this.add('div', '');
      grid.className = 'performance-grid';
      const chart = this.add('div', '', grid);
      this.histogram = new PixelLab.Histogram(chart, {title:'Caller-observed latency', unit:'ms', bins:24, layout:'overlay',
        subtitle:'Shared bin boundaries · blue: native OpenAI · purple: Gauge · legend toggles either arm'});
      const scroll = this.add('div', '', grid);
      scroll.className = 'performance-table-scroll';
      const table = this.add('table', '', scroll);
      table.setAttribute('aria-label','Native versus Gauge performance');
      const head = this.add('tr', '', this.add('thead', '', table));
      for (const title of ['Metric','A · Native OpenAI','B · Gauge','B − A']) this.add('th',title,head).scope='col';
      this.rows = this.add('tbody','',table);
      this.pairs = this.add('p','');
      this.add('p','Percentiles use successful complete outputs; errors and incomplete outputs remain counted above. Throughput excludes idle time between batches. No latency threshold is treated as proof of zero overhead.');
      const details = this.add('details','');
      this.add('summary','Measurement method',details);
      this.method = this.add('p','',details);
    }
    renderHistogram() {
      if (!this.data || !this.histogram) return;
      this.histogram.update(['native','gauge'].map((arm,i) => ({id:arm,
        label:i ? 'B · Gauge client' : 'A · Native OpenAI',color:i ? 'purple' : 'blue',
        values:this.data.arms[arm][this.allAttempts ? 'attempt_latencies_ms' : 'latencies_ms']})));
    }
    destroy() { this.histogram?.destroy(); this.histogram = null; }
  }
  root.GaugePerformanceSummary = PerformanceSummary;
})(globalThis);
