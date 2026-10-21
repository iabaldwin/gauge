# Semantic judging with JEV

Gauge's separate backend automatically judges completed, text-only comparisons
when `PORTKEY_API_KEY` is present. Adopting applications still only swap their
OpenAI client import. The SDK does not import the judge, contact Portkey, or wait
for comparisons or judgments.

```sh
export PORTKEY_API_KEY='your-portkey-key'
gauge/.venv/bin/python tools/gauge_backend.py start --workers 12
```

Restart an already running backend after changing its environment. Set
`GAUGE_JUDGE=off` in the backend environment to disable judging. This integration
uses the explicit API key; it does not read agent login caches or start OAuth.

The packaged adapter sends `@openrouter/typesafe/jev-1.13` to `/v1/decisions` with
`x-portkey-custom-host: https://openrouter.ai/api/alpha`. The default gateway is
`https://cybertron-service-gateway.doordash.team/v1`. `GAUGE_JEV_BASE_URL` overrides
that gateway for a backend deployment. No agent CLI or plugin is required to run
an installed Gauge package.

## What is judged

One JEV request contains the reconstructed conversation, its original
system/developer instructions, and the two complete replies. Model names are
excluded from this state. Three independent choice questions share that context:

- Semantic agreement: **equivalent**, **different**, or **uncertain**.
- Original reply compliance: **compliant**, **violation**, or **uncertain**.
- Candidate reply compliance: the same three options.

Neither reply is ground truth. A material difference can be an improvement.
Compliance checks only the instructions and observed conversation; it cannot
establish external facts, tool success, or overall task success.

The `semantic-v2` rubric judges interchangeability for the final user turn. Social
closings remain equivalent despite differing optional offers or conditional
requests for details. It does not infer a completed booking from a well-wish.
The rubric abstains when the chosen option is uncertain, confidence
is below 0.75, or the two leading option probabilities differ by less than 0.20.
These are initial operating thresholds, not measured accuracy guarantees. Tune
against human-labeled examples before relying on the scores for decisions.

Each persisted comparison has a `judgment` containing its status, gated verdict,
raw choice, per-option probabilities, confidence, model, rubric version, and
recorded timestamps. All three question results remain under `judgment.answers`.
The dashboard shows the semantic badge, confidence, and a separate semantic
agreement denominator. The inspector exposes probabilities and both compliance
verdicts. Uncertain and unjudged pairs are excluded from semantic agreement.
Literal exact agreement and dataset gold-label scores remain independent.

## Isolation and limits

Two dedicated judge workers consume a queue capped at 256 pairs. They use
independent HTTP clients, a 10-second HTTP timeout, no retries or redirects, a
128 KiB request limit, and a 64 KiB response limit. At most 3,000 pairs are
admitted per backend process lifetime, including failed attempts. Comparison
workers enqueue without waiting for JEV. Queue overflow, missing credentials,
unsupported context, exhausted budget, and judge errors never change saved
source or candidate outputs, comparison latency, or provider success status.

The queue is disposable. Restarted pending/running judgments become explicitly
skipped; the backend does not automatically replay saved work or spend quota.
Verdicts survive restart in the same SQLite database as the comparisons.

## Judge existing saved results

Backfill is explicit and bounded; it does not repeat inference calls:

```sh
gauge/.venv/bin/python tools/gauge_backend.py judge --run-id RUN_ID --limit 30
# Or judge just one captured exchange and its three candidate replies:
gauge/.venv/bin/python tools/gauge_backend.py judge --call-id CALL_ID --limit 3
```

The `POST /v1/judgments` endpoint accepts `run_id`, `call_id`, and
`limit` (1–100, default 30). Selection starts with the newest eligible saved
pairs. Completed and uncertain judgments using the current rubric are not resubmitted.
Older rubric versions and failed/skipped judgments can be retried explicitly.
When an older rubric is replaced, its verdict is retained in `judgment_history`
(up to ten previous verdicts per comparison). `/stats` exposes independent judge queue,
active, attempted, completed, uncertain, and error counts under `judging`.

## Code map

- `backend/judge.py`: request contract, rubric, strict distribution validation.
- `backend/judging.py`: bounded scheduling, workers, error isolation, budgets.
- `backend/store.py`: atomic judgment patches, deduplication, saved selection.
- `dashboard/gauge-adapter.js`: maps persisted decisions into viewer records.
- `pixel-lab/`: reusable, provider-independent rendering and aggregation only.
