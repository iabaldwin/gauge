# Gauge backend and Pixel Lab dashboard

Run your existing tests with the Gauge OpenAI client, inspect results from the
terminal, then open the same run in the browser:

```sh
gauge requests
gauge tui
gauge dashboard
```

No Gauge login, authentication token, or signup. Project/run IDs segment the
saved results. Captures, comparisons, and judgments persist in SQLite. The local
service starts independently from the terminal; stopping tests or closing the
browser does not stop it.

## Views

The dashboard starts with model comparison tables and histograms. Request details
open on full ordered context: instructions, system/developer/user/assistant messages,
tool calls/results, multipart inputs, tool definitions, and other fields. Generated
outputs and diff views remain separate. Provider-held context not present inline
is explicitly identified. The main page uses document scrolling.

Original and candidate results have independent status. Semantic judgments,
uncertainty, literal agreement, latency, reasoning/non-reasoning tokens, and unknown
counters retain their distinct meanings. OpenAI output is a comparison reference,
not ground truth. Capture-only requests remain inspectable without providers.

## Configure and compare

```sh
# Set FIREWORKS_API_KEY in the environment that starts the backend.
gauge backend start
gauge configure --budget 300
gauge compare --limit 100
gauge status
```

The default policy compares three Fireworks models; JEV uses backend Portkey
configuration when available. Nothing runs on application inference threads.
`gauge configure --automatic` enables backend polling for newly captured work.
The candidate-call budget is persisted and spent jobs are not repeated on restart.

`gauge backend stop` requires drained comparison and judge queues. Restart after
changing backend provider credentials. See [console commands](../docs/console.md).

## Internal shared backend

```sh
python -m gauge.backend --projects-dir /path/to/projects --db /path/to/service.sqlite --host 0.0.0.0
```

Connect a developer project with `gauge connect https://YOUR-INTERNAL-GAUGE`.
The backend registers a namespace with the existing local project ID. Captures and
run metadata retain their IDs. The dashboard's project selector scopes requests,
queues, exports, and cleanup to that namespace. Stores and comparison budgets are
separate per project. Project IDs do not impose permissions.

## Components and endpoints

Pixel Lab remains a standalone library. The Gauge adapter maps captures into its
records; the app provides project/run selection and polling. Wheels bundle these
same sources with no frontend build requirement.

- `GET/POST /v1/projects`: list/register project namespaces.
- `GET /v1/runs`, `/v1/dashboard`, `/v1/traces`, `/stats`: selected project data.
- `POST /v1/captures`: idempotent ingestion; `POST /v1/import`: capture import without dispatch.
- `POST /v1/runs/import`: preserve imported run metadata.
- `POST /v1/config`: comparison policy; `POST /v1/comparisons`: bounded dispatch.
- `POST /v1/judgments`: bounded judging; `POST /control`: pause/resume.
- `POST /v1/runs/clear/preview` and `/v1/runs/clear`: preview, backup, and delete.

Pass `project_id` as a query parameter on shared-service calls. Ingestion can also
select the namespace using the capture's project ID. There is no Authorization
header, session cookie, ticket, or login endpoint.

## Storage outages

Finished comparisons and judgments remain visible when their final SQLite write
fails. The dashboard and CLI show a storage warning; affected records expose
`persistence.status = "retrying"`, and `/stats` reports `pending_result_writes`.
Only writes are retried, with backoff capped at 30 seconds. The original provider
response, judgment, and attempt IDs are retained; inference is never replayed.

Each backend worker retains at most one finished outcome and waits for persistence
before taking another job. A prolonged storage outage therefore slows backend
work and can fill its bounded queues without blocking the application's OpenAI
calls. Once storage recovers, writes finish and workers resume automatically.

Outcomes awaiting storage are held in memory, not yet durable. If the service is
terminated before a successful write, restart marks the old unfinished records
unavailable rather than repeating paid inference.

## Verify without inference

```sh
node --test gauge/dashboard/test/*.test.cjs
node gauge/dashboard/test/browser.cjs
```

Browser checks use a fixture provider and a temporary database. They cover direct
loading, context/output inspection, persisted runs, queues, cleanup, and restart.
