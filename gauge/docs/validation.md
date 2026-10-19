# Public-data validation

Gauge validation measures two different things: whether capture/correlation/replay
works, and whether model outputs match independently supplied dataset labels.
Literal agreement with OpenAI is never substituted for accuracy.

## Code decomposition

```text
gauge/
  validation/                 # benchmark process; not imported by the SDK
    records.py                # Scenario and checksummed manifest contract
    sources.py                # immutable source URLs, cache, checksums, attribution
    datasets.py               # BANKING77 / MultiWOZ / SQuAD format adapters
    suite.py                  # seeded sampling and versioned prompt contracts
    protocols.py              # ordinary native OpenAI client calls
    prefixes.py               # recorded conversation prefixes and independent audit
    drain.py                  # comparison/judge drain and bounded judge backfill
    checks.py                 # hidden-oracle correlation checks and offline replay
    scoring.py                # gold-label accuracy, JSON validity, SQuAD EM/F1
    backend.py                # internal loopback control/feed client
    runner.py                 # budgets, concurrency, execution and artifact lifecycle
    report.py                 # portable Pixel Lab report; no model invocation
    __main__.py               # argument parsing and command routing only
  backend/
    correlation.py            # evidence-based trace/workload reconstruction
    replay.py                 # bounded text context materialization
    server.py                 # internal ingestion + isolated comparison workers
    store.py                  # SQLite persistence and read models
```

Paths above are relative to `gauge/src/gauge`. Provider calls only occur in the
protocol driver and the separate backend worker. Dataset adapters have no provider
clients; scoring has no network access. Correlation receives capture envelopes,
never dataset identifiers, labels, expected parent links, or benchmark annotations.
The serving SDK and its byte/queue/time budgets are unchanged.

## Data and profiles

The first milestone includes:

| Dataset | Source / split | Smoke calls | Full calls | Scoring |
| --- | --- | ---: | ---: | --- |
| BANKING77 | PolyAI, test | 10 | 400 | Exact intent-label accuracy |
| SQuAD 2.0 | validation, balanced answerable/unanswerable | 8 | 200 | JSON validity, normalized exact match, token F1 |
| MultiWOZ 2.2 | test shard 001 | 32 | 400 | Capture and correlation only |
| Total | | 50 | 1,000 | |

With `prepare --fixed-prefix`, MultiWOZ uses eight smoke / 100 full distinct
dialogue excerpts, each four user turns long. Half use Chat Completions and half
use Responses. Each request contains its complete recorded prefix:

```text
u1                         -> e1
u1, a1, u2                 -> e2
u1, a1, u2, a2, u3         -> e3
```

Here `a1` and `a2` are dataset replies; generated outputs never enter later
requests. The dispatcher builds these independent cases, and the backend compares
each case normally. No response-chain or Conversation IDs are required. The
report audits every captured request against its recorded prefix.

Without that flag, the legacy protocol-validation profile uses two smoke / 25
full excerpts across four protocols: Chat Completions with full history,
Responses with full history, Responses with `previous_response_id`, and Responses
with Conversations. That profile uses generated replies in live continuations.

This is scripted dialogue replay, not a task-success benchmark: later dataset user
turns were written for historical assistant replies and may not fit a new reply.
Dialogue has no independent gold accuracy score. JEV assesses semantic agreement
and instruction compliance for complete output pairs; OpenAI is a comparison
baseline, not ground truth. CLINC/OOS, WildChat, BFCL, GSM8K, tool
execution, and independent closed-loop candidate rollouts are later milestones;
this implementation does not claim to support them.

Sources use immutable commit IDs. Downloaded files have SHA-256 receipts;
corrupt/incomplete cache entries fail closed. Selection is seeded. The manifest
includes source revisions, source-row IDs, split, license, attribution, prompt
contracts, call counts, expected labels and its own checksum. Keep manifests and
reports with published results. Dataset contents, caches and outputs stay outside
Git. BANKING77 is CC-BY-4.0, MultiWOZ's source release is MIT, and SQuAD 2.0 is
CC-BY-SA-4.0; preserve their attribution and applicable redistribution terms.

## Run it

Install the optional benchmark dependencies from the repository root:

```sh
uv sync --project gauge --extra validation --extra dev
```

Prepare a fixture set (downloads once; no inference):

```sh
gauge/.venv/bin/python -m gauge.validation prepare \
  --profile smoke --fixed-prefix --output out/validation-smoke.manifest.json
```

Validate correlation offline using real dataset text plus adversarial duplicate
calls and shuffled/interleaved delivery (no inference):

```sh
gauge/.venv/bin/python -m gauge.validation check \
  out/validation-smoke.manifest.json --output out/validation-smoke-offline.json
```

Run against an idle managed backend with source/provider credentials configured:

```sh
gauge/.venv/bin/gauge backend start
gauge/.venv/bin/python -m gauge.validation run \
  out/validation-smoke.manifest.json --output out/validation-smoke.json
```

The source defaults to `gpt-4.1-nano` with direct OpenAI credentials, or
`@openai-foundations/gpt-5.4-mini` through the configured Portkey gateway. Choose an
explicit `--source-model` for a different supported model/endpoint. The backend
compares Qwen3.8 Max, GPT-OSS-120B, and DeepSeek v4.1 Flash on Fireworks, using
independent clients. Only GPT-OSS receives `reasoning_effort=low`; the other models
use their defaults. These settings and the token caps define this experiment;
quality results are not provider-default leaderboard scores.

For the full first milestone:

```sh
gauge/.venv/bin/python -m gauge.validation prepare \
  --profile full --fixed-prefix --output out/validation-full.manifest.json
gauge/.venv/bin/python -m gauge.validation check \
  out/validation-full.manifest.json --output out/validation-full-offline.json
gauge/.venv/bin/python -m gauge.validation run \
  out/validation-full.manifest.json --concurrency 8 --output out/validation-full.json
```

The runner saves a named backend run, full JSON evidence, and a standalone HTML
report using the shared Pixel Lab library. It leaves the service running. The
manifest is also part of the run's provenance; keep the manifest beside the report.

## What the checks establish

The harness records expected episode membership and expected parent response IDs
separately. It maps ordinary returned response IDs to exported captures after
execution. It does not send benchmark IDs in API metadata, headers, or prompts.

Explicit response links establish parents, and conversation IDs establish common
membership without inventing turn order. Full history without IDs is marked
unresolved; similar content alone never merges conversations. Provider response
IDs are scoped by endpoint. Missing or duplicate response IDs and cycles are
unresolved. Workloads fingerprint instructions, tool definitions and output
contracts, excluding changing user content and model identity.

`GET /v1/traces?run_id=...` returns the graph reconstructed from persisted captures
under the same read authentication as the dashboard. IDs remain stable across
reconstruction. The graph is a read model, not SDK instrumentation.

Text-only explicit Response chains can be replayed when every parent is captured.
The backend materializes the original request/response history for each candidate;
it never forwards OpenAI state IDs to Fireworks or retrieves remote provider state.
Current-turn instructions are applied according to Responses semantics. Missing
parents, tools, unsupported parameters, cycles, >64-link chains, and expanded
contexts above 64 KiB are skipped. Conversations remain capture-only because a
conversation ID does not prove the backend observed all server-held context.

Expected comparison counts for fixed prefixes:

| Profile | Original captures | Candidate jobs | Completed if providers succeed |
| --- | ---: | ---: | ---: |
| Smoke | 50 | 150 | 150 |
| Full | 1,000 | 3,000 | 3,000 |

The legacy protocol-validation profile deliberately skips remote Conversations:

| Profile | Original captures | Candidate jobs | Completed if providers succeed | Deliberately skipped |
| --- | ---: | ---: | ---: | ---: |
| Smoke | 50 | 150 | 126 | 24 |
| Full | 1,000 | 3,000 | 2,700 | 300 |

Transport success and usable output are separate. Token-limited/incomplete
candidate outputs remain unscored and visible in quality coverage. Wrong labels
and invalid JSON receive zero, not an unscored result. Quality is calculated for
the source and each candidate independently, with scored/total denominators.

Each run checks call completion, capture delivery, explicit links, false merges,
conversation splits, per-capture candidate joins, supported replay coverage, queue
drops, comparison errors, and queue recovery. These are infrastructure checks;
they do not require every model answer to be correct.

## Limits

A full run makes up to 1,000 source model calls and 3,000 candidate calls. Fixed
prefixes cap source output at 1,024 tokens and candidate output at 2,048 tokens
including reasoning. The legacy protocol profile uses 256 / 1,024 tokens.
The smoke run is a separate 50-call run. Conversation creation is an additional
API operation, not an inference call. SDK retries are disabled. Episode concurrency
is 1–20, queue capacity is 3,000, and the comparison drain deadline defaults to
20 minutes (`--wait-seconds`, maximum one hour).

Provider keys remain in process configuration, never reports or browser assets.
Fixed-prefix runs require an idle backend with JEV enabled and a fresh judge
budget. The first case checks the source, all three candidates, and JEV before
dispatching the remainder. The runner waits for judgments and makes one bounded
backfill pass for missing/error judgments, without repeating provider inference.
Judge retries count against the backend's configured budget (normally 3,000).
Errors and truncated outputs remain visible in coverage and final checks.

Failed source turns do not prevent later independent fixed-prefix cases. In the
legacy profile they stop their own episode. No profile executes tools automatically.
