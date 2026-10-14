# Gauge backend dashboard

The backend owns the dashboard, run history, captures, and comparison results.
Pixel Lab remains a standalone library. The stress-test runner connects to the
backend and exits without stopping it.

The default view is a model comparison table followed by side-by-side response
comparisons. It reports exact output agreement with its sample count, completed
coverage, paired median latency, mean final-answer/reasoning tokens (plus non-reasoning output), errors, and skipped
jobs. Filter by candidate, status, different outputs, or request/output text.
Inspect a row for full input, output, and timing details; the system trace is
collapsed inside those details. `ComparisonSummary` lives in the standalone
Pixel Lab library and uses its existing provider-independent request contract.

The distribution panel shows latency, reasoning tokens, and non-reasoning
output as histograms. Legends report sample sizes, median, and p95, and let
you toggle models. Hover/focus bars for counts or change the bin resolution.
Failed/skipped jobs are excluded. Token counters that were not reported are
omitted rather than plotted as zero. Original measurements are deduplicated
when multiple candidate models share the same request.

The live comparison queue strip shows queued, active, completed, failed, and
queue-dropped jobs, plus a completion bar, worker count, and last-update time.
It polls with the results about every 1.5 seconds after each fetch; this is polling,
not a push stream. Active runs and the live/all-captures view use backend queue counters.
Historical runs show persisted job counts from their own loaded records, labeled
Saved comparison jobs; they never show another run's queue metrics. Pausing result updates
does not pause comparison execution or freeze these queue counters.

## Start and open

From the repository root, with `FIREWORKS_API_KEY` already in the environment:

```sh
gauge/.venv/bin/python tools/gauge_backend.py start
gauge/.venv/bin/python tools/gauge_backend.py open
```

The second command prints a one-use dashboard link, valid for 30 minutes. Open
that link in your browser. It establishes a read-only HttpOnly, SameSite session
and redirects to **http://127.0.0.1:8765/dashboard**. Reloading and backend restarts
retain the browser session for seven days. Provider credentials are never sent to
the browser. The session cookie does not authorize control or ingestion POSTs.
Manual connection using a Gauge key remains available.

The manager starts the backend as an independent background process, with:

- Database: `out/gauge-backend.sqlite`.
- Logs: `.gauge/backend.log`.
- Local management state/key: `.gauge/backend.json` (mode 0600, ignored by Git).

```sh
gauge/.venv/bin/python tools/gauge_backend.py status
gauge/.venv/bin/python tools/gauge_backend.py stop
```

Closing the dashboard or completing a test does not stop the backend. An explicit
stop or machine restart does; run `start` again to reopen the same database and
saved runs. This is not an OS boot-time service.

## Run a saturation test against it

```sh
gauge/.venv/bin/python tools/live_gauge_saturation.py \
  --calls 200 --baseline-calls 20 --concurrency 20 \
  --comparison-capacity 4 --comparison-max-calls 8 \
  --source-model @openai-foundations/gpt-5.4-mini \
  --output out/gauge-live-saturation-200.json
```

This makes real provider calls. The runner reads the managed backend's connection
configuration, starts a named run, drives native and Gauge phases, saves its
summary to the backend, and leaves the service running. Every new run gets a
unique ID, so prior results remain available even if the JSON report filename is
reused. The caller SDK still adds no annotations: the dedicated local backend
associates captures with its active run. Do not send unrelated application traffic
to this backend during a saturation run. Concurrent test runs are rejected.

For an independently started backend use `--backend-url http://127.0.0.1:PORT`
and its `GAUGE_API_KEY` environment variable. Provider keys belong in the backend
environment; the runner needs its normal OpenAI/gateway credentials.

To import an older test before starting the manager:

```sh
gauge/.venv/bin/python tools/gauge_backend.py start \
  --import-report out/gauge-live-saturation-200.json
```

Import copies captures and existing comparison results into the managed database
without replaying any model calls. The source database/report is preserved. Stop
the backend first if importing into an already running managed service.

## Browse saved results

Use **Saved run** to select a run. Completed runs initially filter the request
explorer to **Compared**, making successful comparisons immediately visible.
Choose **All comparisons** to include pending, failed, and skipped jobs.

The feed is paginated in 100-record pages. The adapter loads up to 1,000 records
per view, displaying loaded/total counts; every supported stress run has at most
1,000 calls. Older runs remain independently selectable. Native baseline calls are
included in the saved run summary, not fabricated as SDK captures. The current
200-call run has 20 native baseline calls and 180 actual captures.

**Pause updates** pauses display updates only. The comparison worker's fault
injection is controlled separately by the test runner.

## App/library boundary

- `pixel-lab/`: unchanged reusable components, theme, interactions and data contract.
- `gauge/dashboard/gauge-adapter.js`: Gauge schema mapping, authenticated polling,
  pagination, reconnect backoff, and change detection.
- `gauge/dashboard/app.js`: saved-run picker, browser session, app mount.
- `gauge/src/gauge/backend/store.py`: durable run/capture/comparison read model.
- `gauge/src/gauge/backend/server.py`: static assets, ingestion, worker, and APIs.
- `tools/gauge_backend.py`: persistent local process management and historical import.

Read endpoints: `/v1/runs` and `/v1/dashboard?run_id=...&before=...`.
Bearer-authenticated test control: `/v1/runs/start`, `/v1/runs/end`, `/control`.
The existing `/v1/captures` SDK ingestion endpoint is unchanged.

Polling is every 1.5 seconds, one snapshot at a time, with a five-second timeout
and retry backoff up to 15 seconds. A 401 stops polling until reconnection.
Completed outcomes survive restart. Interrupted pending/running jobs are marked
skipped; interrupted runs remain visible. Nothing is automatically replayed.

The local worker runs one configured Fireworks candidate and supports its existing
text-only Chat Completions replay parameters. Pixel Lab independently supports
multiple candidates. Responses captures can be displayed but are not replayed by
this local worker. Agreement is literal output agreement, not a correctness grade.
Missing counters and missing comparison history stay unknown. Final-answer tokens
require provider-reported text counters; total minus reasoning is labeled
non-reasoning output, which may include formatting or other output tokens.
The dashboard feed is schema version 2, with a `comparisons` array per capture;
its reader also accepts saved schema-1 feeds. Existing databases migrate
transactionally from one comparison per call to a `(call_id, candidate)` key. Timelines
use measured original latency and recorded backend timestamps; cross-process
wall-clock offsets are approximate and clamped for clock skew.

This is a loopback development backend with a shared
local key and no tenant isolation. Only one backend process should own a database.
The dashboard sees received captures, not SDK events lost before ingestion.

## Verification without provider calls

```sh
gauge/.venv/bin/python -m pytest gauge/tests -q
node --test gauge/dashboard/test/*.test.cjs
# Browser dependencies: cd pixel-lab && npm ci
node gauge/dashboard/test/browser.cjs
```

Browser tests use an explicitly fake provider behind the real HTTP service. They
cover ingestion, correlation, failures, queue saturation, saved runs, session
refresh, and server restart. The runner lifecycle tests verify that success and
failure both save a run and release dispatch without owning the backend process.

## Larger three-model load test

The runner supports up to 1,000 total OpenAI calls and three candidate models.
`--mode load` keeps comparison dispatch enabled; `--mode saturation` deliberately
pauses dispatch to exercise queue overflow. The SDK remains unchanged in both modes.

For a full 1,000 captured requests × three models, configure an idle managed backend
with 12 workers. Stop/start preserves its database and saved runs:

```sh
gauge/.venv/bin/python tools/gauge_backend.py stop
gauge/.venv/bin/python tools/gauge_backend.py start --workers 12
gauge/.venv/bin/python tools/live_gauge_saturation.py \
  --mode load --calls 1000 --baseline-calls 0 --concurrency 20 \
  --comparison-capacity 3000 --comparison-max-calls 3000 \
  --candidate-model accounts/fireworks/models/qwen3p8-max \
  --candidate-model accounts/fireworks/models/gpt-oss-120b \
  --candidate-model accounts/fireworks/models/deepseek-v4p1-flash \
  --output out/gauge-live-load-1000.json
```

This makes up to 1,000 OpenAI calls and 3,000 Fireworks calls, with SDK retries
disabled. The first OpenAI request counts toward the budget and checks source
access before dispatching the remainder. The default source is `gpt-4.1-nano`
when `OPENAI_API_KEY` is set; override it with `--source-model` if needed. Model
availability is account-dependent; check candidates before a large run.
Comparison output is capped at 256 tokens per call, including reasoning.

`--baseline-calls 0` allocates the entire OpenAI budget to captured requests; it
provides no native-client baseline for estimating instrumentation overhead.
The fixture repeats four classification inputs, so this tests load and correlation,
not general model quality. Queue and call budgets are bounded, and local backend
workers are limited to 16. The dashboard can load all 1,000 captures with their
three comparison results. Default comparison drain time is 20 minutes; adjust
`--comparison-wait-seconds` up to 3,600 if needed. Missing captures, dropped jobs,
provider errors, and incomplete recovery fail the saved run checks.
