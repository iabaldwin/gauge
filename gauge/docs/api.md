# Gauge initial API scope

Status: `0.1.0a1`; implemented foundation with explicit backend extension points.

## Two integration surfaces

Adopting applications only swap/wrap their client. Dataset generation, model
selection, comparison policy, and grading are backend responsibilities. The
backend API below must not become extra per-task application instrumentation.

| Surface | Contract | Current implementation |
| --- | --- | --- |
| `wrap(client) -> client` | Keep native inference behavior | Instance instrumentation; sync/async OpenAI create/parse; cloned clients |
| `gauge.openai.OpenAI`, `AsyncOpenAI` | Import replacement | Native SDK subclasses applying the same wrapper |
| `CaptureBuffer` | Bounded capture sink | Immutable wire bytes; nonblocking enqueue; byte/count budgets; daemon HTTP export |
| `Case(id, input, provenance)` | An unlabeled input with lineage | Text cases; no inferred `expected` field |
| `generate_cases(...) -> list[Case]` | Generate a shared input dataset | One model call; strict JSON validation; no auto-retries |
| `OpenAITarget(...)` | A provider/model/API/configuration | Async native Responses or compatible Chat Completions |
| `Experiment(...).run(cases) -> Report` | Concurrent reference/candidate dispatch | Bounded worker pool, deadlines, call-count preflight, cancellation |
| `ExactMatch` | Reference agreement | Literal equality, not semantic equality or accuracy |
| `Grader.grade(...) -> Grade` | Independent assessment | Protocol, accepted by Experiment; implementation supplied by backend |

## Capture contract

```python
def wrap(client: ClientT, *, buffer: CaptureBuffer | None = None) -> ClientT: ...
```

`buffer` is an internal/testing extension; normal adopters only swap or wrap their
client. Wrapping twice is idempotent and keeps the original binding. Unsupported
client types raise a usage error. Instrumentation/setup failures on supported native
clients fail open and roll back. OpenAI 3.29.x is the tested private-hook integration;
other installed SDK versions pass through without capture. Backend modules load
lazily, so importing the client does not import comparison or ingestion code.

Gauge wraps the logical native request plus request-building/response-processing
boundaries. Native retries and parsing are unchanged. An event contains bounded
immutable request/response bytes, an internally generated ID, timestamp, latency,
operation, attempt count, optional provider request ID, and an explicit omission
reason. No application serializers, custom reprs, or body readers are invoked.
A stream/raw streaming response remains lazy. Native `parse()` is captured as its
wire operation (`responses.create` or `chat.completions.create`).

Each wire body is limited to 65,536 bytes before retention; oversized bodies are
omitted, not truncated into misleading JSON. Pending events and diagnostic history
are each bounded by count (100) and accounted bytes (4 MiB), plus one in-flight
event. Accounting includes a 2 KiB metadata allowance per event; these are capture
budgets, not a bound on native SDK memory or total interpreter RSS. JSON decoding
runs in the worker with byte, node, and depth limits. Malformed captures are dropped.

`CaptureEvent.status=completed` means the native logical request returned normally.
Provider refusals/incomplete outputs remain visible. Backend replay eligibility is
separate and conservative. Headers/authentication and provider error bodies are
excluded; full successful JSON bodies, including application metadata, are retained.

Enqueue never waits for the queue lock or capacity. Failure/backpressure only loses
capture. The daemon uses a dedicated HTTP client, finite retry count, timeouts, and a
circuit breaker. It never runs candidate models or graders. `GAUGE_ENDPOINT` enables
direct export to a separate backend; otherwise captures persist locally.
`GAUGE_ENABLED=0` disables capture. Gauge requires no authentication. See [the wire/ingestion contract](ingestion.md).

`flush(timeout=5)` optionally waits for submissions made before the call; `drain()`
returns available events. Neither is called by inference. `close()` never joins.
Pending data can be lost at shutdown. After fork, inherited capture is disabled
until client setup restarts it; stale queues/locks/transports are discarded.
Evaluation helpers suppress capture to avoid recursive collection if used with a
wrapped backend client, although production comparisons belong in a separate service.

## Synthetic generation

```python
async def generate_cases(
    *,
    generator: Target,
    task: str,
    count: int = 20,
    seed_inputs: Sequence[str] = (),
    timeout: float = 60,
) -> list[Case]: ...
```

One generator call returns exactly `count` distinct nonempty input strings
(1–100). Invalid, refused, partial, or malformed generation fails before reference
and candidate dispatch. Generated answers are not accepted as labels. Each case
records generation ID, generator config, task, seed inputs, generation instruction,
and source response ID when available. Generation is a separate call budget from
the experiment. Dataset creation is completed before comparisons begin.

## Model dispatch

```python
OpenAITarget(
    name="reference",
    client=async_client,
    model="gpt-5.6-terra",
    api="responses",
    parameters={"max_output_tokens": 4096},
)

Experiment(
    instructions="...",
    reference=reference,
    candidates=[candidate],
    max_concurrency=8,
    max_cases=1000,
    max_calls=10000,
    timeout=60,
    grader=None,
)
```

Only text-only single-turn requests are portable in this implementation. Responses
receives `instructions` + `input`; Chat Completions receives system + user messages.
Provider-specific parameters are retained separately and limited to an explicit
allowlist. Structured-output schemas may be supplied with `text` or
`response_format`; support is determined by the chosen provider/model.

`previous_response_id`, tools, images, arbitrary native payload replay, and provider
state are not silently converted. Serving-side capture can record these requests,
but the initial text experiment adapter does not replay them.

All case IDs and target names must be unique/nonempty. Case and call limits are
checked before any invocation. A shared bounded worker pool dispatches reference
and candidates independently; concurrency is at least two. They receive copies
of the same case and task instructions, never each other's output. Completion
order does not change report order. Per-call deadlines include SDK retries;
`max_calls` counts logical invocations, not hidden SDK attempts. Callers control
native retry settings; the live example uses `max_retries=0`. This is not a dollar
budget or a requests-per-minute limiter. Default output caps are 4096 tokens.

Provider errors/timeouts become per-target outcome records without cancelling
other cases. Outer cancellation propagates and cancels in-flight workers. Refusals,
incomplete outputs, and unsupported outputs are excluded from agreement scoring.
Failures remain visible in the total/not-scored counts. Exceptions retain their
type and HTTP status when available, avoiding raw provider error-body capture.
Clients remain caller-owned. `Report.diagnostics()` groups failure and response
statuses by target. The live example separately probes each candidate before any
generation/reference calls; failed candidates are skipped and retained in report
metadata. If all fail, the job stops. These probes are outside the experiment's call
limit. The default policy selects Qwen3.8 Max, GPT-OSS 120B, and DeepSeek v4.1 Flash; `FIREWORKS_MODELS`
overrides it with a comma-separated list. Catalog model availability is not inference availability.

## Report and grading lineage

`Report` has a UUID, schema version, timestamp, full cases/provenance, task
instructions, target descriptions, ordered outcomes, and comparisons. Each outcome
retains latency, native request/response and usage, or an error classification.
`Report.to_json()` produces the backend interchange representation.
`Report.from_dict()` loads the current report schema and recomputes derived token
counters from provider usage. `Report.to_html()` produces a
self-contained escaped HTML report with search, filters, and all model columns.
The local CLI `python -m gauge report <path.json>` renders saved results without
inference; `python -m gauge demo` renders explicitly illustrative data. No prices are
hardcoded and token counts are not presented as dollar costs.

Every comparison always contains deterministic `agreement`. If configured, it
also contains an independent `assessment` from `Grader`. Graders receive the case,
instructions and both complete outcome records, including failures. They can
abstain. Grader errors are stored separately and cannot erase a comparison.
The worker/deadline bounds also apply to grader invocations; custom graders must
bound their own internal requests. Gauge suppresses capture while invoking targets,
generators and graders. Graders must treat model outputs as untrusted task data.

```python
class Grader(Protocol):
    async def grade(
        self,
        *,
        case: Case,
        instructions: str,
        reference: Outcome,
        candidate: Outcome,
    ) -> Grade: ...
```

`Grade` contains evaluator identity/version, metric, nullable value, status,
rationale, and metadata. Store rubric ID/version, judge configuration, supporting
evidence, and separate reference/candidate correctness judgments in metadata.
An absent score is not zero. Reference equality is never evidence of accuracy.

Automatic grader creation is a later backend capability: infer a rubric from task
evidence, preserve its provenance, and validate it on independent held-out cases
before making correctness claims. No automatic evaluator-creation API is exposed yet.

## Token accounting contract

`ModelOutput.usage` retains the provider payload. The derived `token_usage`
property normalizes the following fields without retokenizing or estimating text:

| Field | Meaning |
| --- | --- |
| `input_tokens` | Native `input_tokens` or `prompt_tokens` |
| `total_output_tokens` | Native `output_tokens` or `completion_tokens`; includes reasoning |
| `reasoning_tokens` | Explicit `reasoning_tokens` in output/completion details |
| `final_answer_tokens` | Explicit text counter for the answer channel, or zero for a known empty text channel |
| `non_reasoning_output_tokens` | Total minus reported reasoning; may include formatting, not an exact answer count |
| `unattributed_output_tokens` | Total minus reasoning and explicit answer counts, when all are known |

`normalize_usage(usage, text_only=False, answer_text=None)` is the public helper.
Enable `text_only` only when text counters can be attributed to the final-answer
channel. `ModelOutput` enables this for its supported text-only completed or partial
outputs; tool/refusal/unsupported outputs do not receive an answer-token claim.
Final-answer tokens here mean the answer channel, which may be partial; completion
status remains separate. Native reasoning text is not tokenized to infer counts.

Missing values stay `None`/JSON `null`. Duplicated aliases are not added together;
conflicting aliases become unknown and carry an issue. Negative, Boolean, or
non-integer counters are rejected. Inconsistent totals carry an issue and never
produce a negative derived remainder. Sources identify explicit counters, derived
arithmetic, and known-empty answers. Raw counters always remain in `usage`.

`Report.token_summary()` includes both successful and incomplete calls. A metric's
total is null unless every dispatched call has that metric. `known_totals` and
`known_calls` preserve partial information, including coverage gaps from failed
calls. `calls_with_usage_issues` exposes inconsistent reporting. Generation and
preflight calls are not included. Saved JSON includes per-output `token_usage` and
top-level `token_summary`; inference return objects remain unchanged.

Reported non-reasoning tokens need not equal visible answer tokens; see
[OpenAI's output token accounting](https://developers.openai.com/api/docs/guides/token-counting#understand-output-token-counts).

## Next implementation milestones

1. Deploy the implemented project-scoped service on shared internal infrastructure.
   Capture ingestion, candidate dispatch, sampling, and budgets are implemented; Gauge has no auth.
2. Capture-to-dataset generation using the existing correlation evidence.
3. Extend capture coverage for streamed outputs and the native Portkey SDK.
4. Automatic rubric proposals, independent judge calibration, labeled holdouts,
   adjudication of disagreements, and uncertainty-aware recommendations.

The earlier audit described a broader intended compatibility contract. This file
states the narrower implemented release; deferred features are not claimed complete.

## Provider references

- [OpenAI text generation / Responses](https://developers.openai.com/api/docs/guides/text)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [Fireworks OpenAI compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility)

Verified against official documentation on 2026-10-12. Model/account availability
is deployment-specific; Fireworks model IDs are supplied by configuration.
