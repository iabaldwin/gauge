# Gauge capture → separate backend

The application only swaps its client import. Deployment configuration supplies
`GAUGE_ENDPOINT` (the full HTTPS capture URL) and optionally `GAUGE_API_KEY` (Bearer
authentication for Gauge, never a provider key). Without an endpoint, events remain
local. The SDK contains no comparison dispatch, synthetic generation, or graders
on its serving/capture path.

## Delivery

The SDK posts one JSON envelope per logical OpenAI request, after the native
request returns or raises. Export runs on a daemon thread through a private HTTP
client. Each request has `Idempotency-Key: <call_id>`. The default policy allows two
attempts total; retryable HTTP statuses are 408, 429, 500, 502, 503, and 504. Other
non-2xx statuses are terminal for that event. Transport exceptions are retryable.
Three failed events open the circuit for 30 seconds; events arriving while open
are dropped. A later event probes recovery; success resets the circuit.

The application never waits for delivery. A 2xx response acknowledges ingestion
only. The SDK does not follow redirects, read acknowledgement bodies, or wait for
an evaluation. Network timeouts are 2 seconds per operation (10 ms for connection
pool acquisition); they are not a hard wall-clock deadline against a trickling
peer. This cannot block inference: queued events remain bounded and are dropped
under pressure. No durable application-side spool or shutdown flush is required.
Endpoint/token are read when the worker starts. TLS uses normal certificate
verification; HTTP is accepted only for loopback development. The export client
does not inherit proxy environment variables or provider transport configuration.

## Envelope v1

```json
{
  "schema_version": "1",
  "call_id": "call_0123456789abcdef0123456789abcdef",
  "operation": "responses.create",
  "created_at": 1791900000.0,
  "latency_ms": 321.5,
  "status": "completed",
  "request": {"model": "reference-model", "input": "Classify this item"},
  "response": {"id": "resp_original", "status": "completed", "output": []},
  "error": null,
  "provider_request_id": "req_original",
  "endpoint_host": "provider.example",
  "attempts": 1,
  "replay_eligible": false,
  "ineligible_reason": "incomplete_output",
  "reference_source": "original_response"
}
```

`call_id` is created internally once per native logical request, survives delivery
retries, and requires no caller metadata. Native provider retries remain inside
that request; `attempts` counts SDK request builds. The operation is the wire
endpoint, so `parse()` is represented as `responses.create` or
`chat.completions.create`. Provider response IDs remain in the response body.

`status` is `completed`, `error`, `cancelled`, or `stream_passthrough`. Completed
means native request completion, not a correctness judgment. Exception names are
recorded without messages/provider error bodies. Streaming bodies are never read;
there is no stream final-output capture. Raw non-streaming helpers can provide
complete wire capture even though parsing happens later in the application.

Each JSON request/response body has a 65,536-byte capture limit checked before
retention. Omitted requests use a `capture_omitted` marker; missing responses are
null. Oversized bodies are not truncated. The envelope cap is 160,000 bytes.
The worker and validator also limit JSON depth to 32 and node count to 8,192.
Malformed or excessively complex captures are dropped rather than evaluated.
Wire bodies may contain application data, including arbitrary body metadata;
transport/authorization headers and client credentials are never copied.

The backend recomputes eligibility. Errors, partial/refused outputs, unconsumed
streams, missing/oversized bodies, tools, provider-held state, and non-text inputs
are ineligible. Eligibility establishes a conservative independent text-comparison
candidate; a backend adapter must still validate its own supported parameters.
It does not establish ground truth or authorize executing any supplied tools.

## Receiver implementation

`gauge.ingestion` is a backend-only module. `CaptureInbox` supplies a durable SQLite
reference inbox; a deployed service can implement the same uniqueness contract
in its existing database. It is never imported by the client integration.

```python
from gauge.ingestion import CaptureConflict, CaptureInbox

# In the backend HTTP worker, after authenticating and limiting the request body:
inbox = CaptureInbox("captures.sqlite")
call_id, inserted = inbox.accept(raw_request_body)
# Commit has completed. Return 202 for inserted or an identical duplicate.
# CaptureConflict -> 409; malformed/unsupported payload -> 400.
```

Create a connection per backend worker and close it in the service's own lifecycle.
Repeated identical captures acknowledge the existing row, including after restart.
Reusing the ID with different contents raises `CaptureConflict` and cannot replace
the original request/response. Ineligible events can remain available for coverage
and loss analysis but must not enter the comparison queue.

The HTTP service must enforce authentication/tenant isolation and the envelope
size limit while reading, rather than after an unbounded body read. Validate that
its idempotency header matches the body ID. A production datastore should scope
IDs to the authenticated tenant; never derive tenant identity from captured
metadata. Persist before acknowledging, and never wait for a model/grader job in
the ingestion handler. A [loopback reference service and dashboard](../dashboard/README.md)
are available in this repository; production deployment and tenant isolation
remain separate service responsibilities.

## Comparison lineage

Candidate jobs, outputs, and grader results carry `(tenant_id, call_id)` plus the
candidate identity and evaluation-policy/grader versions. Use the captured
`response` as the original reference. Never replace it with a second OpenAI call;
a rerun is a separately identified result. Idempotent job keys must also include
the candidate/policy identity so delivery retries do not launch duplicate work.

The existing `Experiment` API runs independent synthetic datasets. It is not yet a
production-capture replay adapter, and must not be called from the client wrapper.
The separate service owns its clients, connection pools, retry/concurrency/cost
budgets, CPU/memory limits, and provider quota. This SDK change does not deploy or
provision those service resources.

## Verification

From the repository root:

```sh
gauge/.venv/bin/python -m pytest -q gauge/tests
gauge/.venv/bin/python tools/benchmark_gauge_capture.py --calls 1000
```

These use mock HTTP only. Fault tests cover contention, a stalled backend, queue
byte/count pressure, malicious serializers, input/result mutation, instrumentation
rollback, native retries/cancellation, streaming/raw helpers, both HTTPX transports,
fork recovery, process exit without flushing, retry/circuit policy, and durable
idempotent ingestion. The microbenchmark reports percentiles and bounded retained
capture bytes; it does not certify a production latency SLO.
