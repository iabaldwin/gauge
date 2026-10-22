# Pixel Lab

Standalone CSS and JavaScript components for inspecting original inference, asynchronous model comparisons, and recorded traces. No runtime dependencies, build step, network requests, remote fonts, or framework required.

## Open the demo

```sh
open pixel-lab/demo/index.html
```

The demo works directly from disk and gives each part of the library its own tab:

- **Comparisons** opens with model summaries, distributions, response pairs, filters, and request details.
- **Histograms** shows latency, zero reasoning, missing counters, outliers, and empty states. Change datasets and bins, toggle models, inspect bars, or generate a new seeded sample.
- **Request inspector** opens with the full captured context, including instructions, ordered messages, and tool calls/results. Switch candidates, inspect outputs/raw records, compare lines, and copy outputs.
- **Traces & replay** shows the execution map, zoom, node details, span timing, and replay alongside the request explorer and inspector.

Both dashboard layouts support synthetic incoming requests, pause/resume, and JSON export.
Use the navigation with a mouse or arrow keys; each tab has a direct fragment link.
All data and Run test actions are synthetic, with no provider calls. The showcase
adapts to small screens and is included in the package. Components remain independently reusable.

## Embed

```html
<link rel="stylesheet" href="./pixel-lab/pixel-lab.css">
<div id="trace-app"></div>
<script src="./pixel-lab/pixel-lab.js"></script>
<script>
  const dashboard = new PixelLab.Dashboard('#trace-app', {
    title: 'Gauge',
    subtitle: 'Development / run 42',
    maxRequests: 1000,
    layout: 'comparisons', // Or 'trace' for the original trace workspace.
    requests: [],
    // Optional. Without this callback the Run test button is disabled.
    onRun: async (view) => { /* invoke your application's backend */ }
  });
</script>
```

Or import `Dashboard` from `pixel-lab.mjs` through a server/bundler and include the CSS separately. The package exposes CommonJS, ESM, and TypeScript declarations. ESM file URLs are subject to browser module restrictions; use the classic script for direct-from-disk use.

## Data contract

```js
dashboard.upsert({
  id: 'req_123',                       // stable application/backend correlation id
  name: 'Dish soap',
  startedAt: Date.now(),              // Unix milliseconds; newest first
  captured: true,
  input: { messages: [{ role: 'user', content: 'Classify dish soap' }] },
  original: {
    model: 'OpenAI / reference', status: 'succeeded',
    output: { category: 'household' }, durationMs: 420,
    tokens: { input: 42, output: 8, reasoning: 0, answer: 2, nonReasoning: 8, unattributed: 6 }
  },
  candidates: [{
    id: 'qwen', provider: 'Fireworks', model: 'Qwen / candidate',
    status: 'succeeded', output: { category: 'household' }, durationMs: 610,
    tokens: { input: 42, output: 90, reasoning: 82, answer: 2, nonReasoning: 8, unattributed: 6 },
    grade: { label: 'Exact match', explanation: 'Matches the reference label.' }
  }],
  spans: [
    { id: 'original', name: 'OpenAI', kind: 'original', startMs: 0, durationMs: 420, status: 'succeeded' },
    { id: 'qwen', name: 'Qwen', kind: 'candidate', startMs: 500, durationMs: 610, status: 'succeeded' }
  ]
});
```

Pass plain JSON-compatible records. Request ids and per-request candidate ids must be unique. `upsert` replaces a complete record by id; it does not merge partial updates. `setData` replaces the dataset and clears buffered updates. Treat records as immutable after supplying them.

Result states: `pending`, `running`, `succeeded`, `failed`, `skipped`. Use `error` for failure text, `reason` for skip text. Missing candidate output is never invented; failed/skipped jobs never display an output even if the record contains one. Original status remains independent of candidate status. Grades come from supplied backend data; the viewer does not call graders or equate text agreement with accuracy.

`tokens.output` means total completion tokens, including reasoning. `tokens.answer` is independently reported final-answer usage. `tokens.nonReasoning` is total minus reasoning, and can include formatting or other output channels. Unknown counts remain unknown. `normalizeUsage(nativeUsage, {textOnly: true, answerText})` follows Gauge's Python normalization rules and records counter issues/provenance. Missing spans stay missing. Span offsets are relative to the request start; concurrent candidates may overlap. Replay reveals recorded spans over four seconds and does not re-run inference.

Summary counts are **requests** in the retained dataset, not candidate-job counts or server-wide totals. A request is compared if at least one candidate succeeded, skipped only if every candidate was skipped, and has a comparison error if any candidate failed. Requests with mixed terminal candidate states are labeled Partial.

## Public API

### Histograms

Histograms are standalone components, with no charting dependency:

```js
const chart = new PixelLab.Histogram('#latency', {
  title: 'Latency', unit: 'ms', bins: 8
});
chart.update([
  { id: 'original', label: 'OpenAI', values: [420, 530, 610] },
  { id: 'candidate', label: 'Qwen', values: [180, 240, 390] }
]);
```

Set `layout: 'overlay'` for translucent overlapping bars, useful for A/B
comparisons. The default `grouped` layout places bars beside each other.

All series share equal-width bins and a zero-based horizontal axis. The upper
edge of the final bin is inclusive. Unknown, negative, and non-finite values
are excluded; zero is a valid measurement. Legends show each series' sample
count, median, and interpolated p95. Click legends to toggle models; hover or
keyboard-focus bars for ranges and counts. `setBins(6)` changes resolution;
`destroy()` removes listeners and content. `binHistogram(series, bins)` exposes
the computed bins and counts without mounting anything.

The comparisons dashboard automatically includes latency, reasoning-token,
and non-reasoning-token histograms. Latency uses completed original/candidate
pairs. Originals are counted once across candidates; token charts use only
reported counters. The candidate-model filter scopes the distributions.

`ComparisonSummary` is a reusable table view using the same request records:

```js
const comparisons = new PixelLab.ComparisonSummary('#comparisons', {
  onSelect: ({ requestId, candidateId }) => { /* open your request inspector */ }
});
comparisons.update(requests);
```

It shows model summaries and searchable, filterable output pairs. Models are
grouped by candidate provider/model and original model. Completed, failed,
skipped, and waiting jobs have separate counts. Exact output agreement uses only
successful pairs with captured outputs. Paired median latencies exclude failed
originals; mean token counts use reported usage and display unknown values as
`—`. Final-answer tokens require an explicit answer count; non-reasoning output
is shown separately. The pure
`summarizeModels(normalizedRequests)` helper exposes these aggregates.
The table displays 100 output pairs at a time, with a button to show more.

The following methods belong to `Dashboard`:

| Method | Behavior |
| --- | --- |
| `setData(requests)` | Replace the full dataset; validate ids; keep newest `maxRequests` |
| `upsert(request)` / `upsertMany(requests)` | Replace/insert complete records; batches render once |
| `select(id, candidateId?)` / `selected()` | Select a request and optional candidate / get the selected record |
| `setPaused(boolean)` | Pause display updates; buffer newest records by id up to `maxRequests` |
| `on(event, handler)` | Subscribe; returns an unsubscribe function |
| `connect(subscribe)` | Attach a source adapter; returns a disconnect function |
| `disconnect()` | Detach the current adapter |
| `run()` | Invoke the supplied `onRun`, with duplicate-run protection and visible errors |
| `exportData()` / `download()` | Get/export the currently retained records as JSON |
| `destroy()` | Detach source, cancel replay, remove listeners and mounted content |

Events: `request:select`, `candidate:select`, `node:select`, `span:select`, `run:error`. See `pixel-lab.d.ts` for event payloads and component APIs. No global keyboard listeners are installed. Arrow/Home/End navigate focused request buttons and inspector tabs. All HTML from records is escaped; clipboard uses the browser Clipboard API and reports when unavailable.

## Live backend adapter

The library is transport-neutral and does not know a Gauge endpoint. This is an **example contract for your server to implement**, not an endpoint supplied by this package:

```js
const disconnect = dashboard.connect(({ upsert, status, error }) => {
  const stream = new EventSource('/your-request-events');
  stream.onopen = () => status('Connected');
  stream.onmessage = event => {
    try { upsert(JSON.parse(event.data)); }
    catch (e) { error(e); }
  };
  stream.onerror = () => { status('Reconnecting'); error('Event stream interrupted'); };
  return () => stream.close();
});
```

Authentication and redaction belong in your application/backend. Never ship provider credentials to a browser. Pausing the view does not pause the source or model calls. Buffers are bounded by **record count**, not payload bytes; cap and redact large payloads upstream. The explorer uses regular DOM rows, not virtualization; the default cap is 1,000 records. On component teardown call `destroy()`; your `onRun` callback owns cancellation of its own work.

## Independent components

`OutputInspector` opens on **Context** for each newly selected request. It shows
Chat messages and Responses inputs in order, with instructions above them.
Multipart content, tool calls/results, available tool schemas, and unknown fields
are preserved. Request settings have a separate expandable section. Text is
escaped; media and file references are displayed as data without fetching them.
The Outputs, Diff, Input (request JSON), and Raw (complete record) tabs remain.
Provider-held history referenced by an ID is explicitly marked unavailable here;
the component never fetches or invents context.

The context renderer can also be mounted independently of an inspector:

```js
const context = new PixelLab.RequestContext('#request-context');
context.update(nativeRequestBody);
// Pure data adapter: PixelLab.normalizeContext(nativeRequestBody)
context.destroy();
```

```js
const request = PixelLab.normalizeRequest(record);
const graph = new PixelLab.TraceMap('#graph', { onNodeSelect: id => console.log(id) });
graph.update(request, 'qwen');
graph.setZoom(0.9);
// Also: RequestExplorer.update(requests, selectedId)
// OutputInspector.update(request, candidateId)
// Timeline.update(request); Timeline.replay(); Timeline.stop()
graph.destroy();
```

Supply normalized records to independent components. Dashboard normalizes automatically. Components add/remove their own `.pl` scope and replace their mount element's contents. Use a dedicated empty mount. Multiple dashboards/components can coexist.

## Theme and layout

All styles are scoped under `.pl`, with `pl-` classes. Override tokens on your mount:

```css
#trace-app {
  --pl-blue: #0649ff;
  --pl-ink: #142449;
  --pl-yellow: #fff260;
  --pl-paper: #f2f5fa;
  height: 850px;
}
```

Other tokens: `--pl-surface`, `--pl-line`, `--pl-muted`, `--pl-mint`, `--pl-green`, `--pl-orange`, `--pl-red`, `--pl-purple`. Use a modern evergreen browser with CSS grid, `zoom`, and dynamic viewport units. Pixel character comes from squared windows, small geometry, segmented tracks, high-contrast selection, and system monospace; no font download is needed. Reduced-motion and print styles are included. This is a functional implementation of the concept, not a raster overlay or pixel-for-pixel reproduction.

## Development

```sh
cd pixel-lab
npm ci
npm test
npm run test:browser
```

Browser tests use installed Chrome on macOS, or Playwright Chromium when `PIXEL_LAB_CHROME` is unset and Chrome is absent. Set `PIXEL_LAB_CHROME` to an executable if needed. Test-only dependencies are not included in the runtime package. To install Chromium for tests: `npx playwright install chromium`.

The [Gauge dashboard application](../gauge/dashboard/README.md) integrates this library with the local backend through a separate polling adapter. This package and its demo remain standalone; neither imports Gauge code nor contains service credentials or endpoints.

### Semantic judgments

Supply `candidate.judgment` to render backend semantic decisions alongside exact
text agreement. The library never calls a model or infers a semantic verdict.

```js
judgment: {
  status: 'completed', verdict: 'equivalent', confidence: 0.95,
  probabilities: {equivalent: 0.97, different: 0.03, uncertain: 0},
  model: 'your-judge', rubric_version: 'v1'
},
grade: {label: 'Equivalent', explanation: 'Your backend evaluation details.'}
```

`status` also accepts `uncertain`, `pending`, `running`, `error`, and `skipped`.
Semantic agreement includes only completed equivalent/different verdicts paired
with successful original and candidate outputs. Uncertain and unjudged pairs do
not count as disagreements. Exact agreement retains its separate denominator.
