![Gauge — one import, many perspectives](gauge/docs/assets/gauge-header.svg)

# Gauge

**Run your tests. See what other models would have answered.**

Gauge captures OpenAI calls through a client import swap. Inspect saved results
from the terminal, then open the same run in the Pixel Lab dashboard. Comparisons
and judging execute in a separate backend process.

This is an internal tool. Gauge has no login, accounts, or API keys. Project and
run IDs segment results. OpenAI, Fireworks, and judge credentials are provider
configuration and stay in their respective application/backend environments.

## Start with your tests

Install the first-release package from this checkout:

```sh
python -m pip install ./gauge
```

In your application, replace the client import:

```python
from gauge.openai import OpenAI, AsyncOpenAI
```

Keep client construction and calls unchanged. Run your normal pytest command.
The automatically installed pytest plugin groups the run and delivers pending
captures at teardown. No Gauge endpoint, manual annotations, service startup,
flush call, or browser setup is needed.

```sh
pytest
gauge runs list
gauge requests
gauge inspect CALL_ID
gauge tui
gauge dashboard
```

The first terminal command starts the separate local service if needed. Results
already exist in the project's `.gauge/captures.sqlite`; the service reads that
same database. `gauge dashboard` opens the latest run without signing in.

## Compare models

Set `FIREWORKS_API_KEY` in the environment that starts the backend, then:

```sh
gauge configure --budget 300
gauge compare --limit 100
gauge status
```

The default candidates are Qwen3.8 Max, GPT-OSS-120B, and DeepSeek v4.1 Flash.
The budget counts candidate calls, not original requests. `PORTKEY_API_KEY`
enables JEV judging on the backend. Captured originals remain inspectable without
comparison credentials. Starting the CLI or dashboard alone does not spend on
model inference. Enable ongoing dispatch explicitly with `gauge configure --automatic`.

For an internal shared backend, run `python -m gauge.backend --projects-dir PATH`
on the server and use `gauge connect https://YOUR-INTERNAL-GAUGE` locally. Project
namespaces have separate stores and budgets; there is no Gauge authentication.

## Live saturation tests

```sh
make stress-small   # 20 OpenAI calls, concurrency 4
make stress-medium  # 200 OpenAI calls, concurrency 10
make stress-large   # 1,000 OpenAI calls, concurrency 20
```

These targets make paid provider calls. Configure `OPENAI_API_KEY`, or
`PORTKEY_API_KEY` and `PORTKEY_BASE_URL`, plus `FIREWORKS_API_KEY` before starting
the backend. If a backend is already running without comparison credentials,
stop it with `gauge backend stop` first; the runner starts it again.

Each run includes four native baseline calls; the remaining calls use Gauge.
All three default Fireworks candidates are configured. The runner pauses the
comparison workers to fill a bounded queue, verifies that OpenAI still completes,
and resumes comparisons. Dropped comparison jobs are expected in this saturation
test; it does not evaluate every original against every candidate.

Requests and results persist in the project's backend database. Timestamped reports
are saved under `out/stress/`, and the runner prints a link to the run's dashboard.
Open the latest run with `gauge dashboard`.

Override the source model with `STRESS_SOURCE_MODEL=MODEL_ID`, the space-separated
candidate list with `STRESS_MODELS="MODEL_ID ..."`, or the report directory with
`STRESS_OUTPUT_DIR=PATH`. The runner defaults to `gpt-4.1-nano` with an OpenAI key
or `@openai-foundations/gpt-5.4-mini` through Portkey.

## Native versus Gauge performance

```sh
make ab-small   # 20 native + 20 Gauge calls
make ab-medium  # 200 native + 200 Gauge calls
make ab-large   # 1,000 native + 1,000 Gauge calls, concurrency 20
```

Requires `OPENAI_API_KEY` and a backend started with `FIREWORKS_API_KEY`.
Uses `gpt-4.1-nano` by default; override with `AB_MODEL=MODEL_ID`.
Each arm runs in a separate persistent process with identical prompts and options.
Matched batches run in seeded randomized A/B order. There are no extra warmups or
provider retries; cold calls and failures stay in the saved measurements.

The Gauge arm dispatches three Fireworks comparisons per capture, with capacity
for all 3,000 jobs on the large run. Workers stay enabled. This measures caller
performance under comparison load; it does not deliberately drop backend work.
Wait for other runs and queues to finish first. After updating backend code, stop
and start the idle backend to load it before running this target.

The printed dashboard link shows live overlaid histograms, p50/p95/p99, active-batch
throughput, errors, and Gauge-minus-native differences. Raw samples and batch order
are saved in `out/stress/ab-*.json`; the histogram data also persists with the backend
run. Errors and incomplete outputs are excluded from successful-latency percentiles
and counted separately; the histogram can show all attempts. Live provider noise
means these are observed differences, not proof of zero overhead.

## Build a distribution

```sh
make build
make check-wheel
```

`make build` creates a wheel and source distribution and checks the bundled UI
assets. `make check-wheel` also validates metadata and installs the wheel in a
temporary environment outside this checkout to verify SDK imports and the CLI.
See [the release guide](gauge/docs/releasing.md) for clean-install verification
and internal-registry publishing. The package is not published to a public index.

## Project layout

- [SDK and usage](gauge/README.md): `gauge/src/gauge/`.
- Automatic identity/storage/service lifecycle: `gauge/src/gauge/local/`.
- [Terminal commands](gauge/docs/console.md): `gauge/src/gauge/cli/`.
- [Backend and dashboard](gauge/dashboard/README.md): `gauge/src/gauge/backend/`, `gauge/dashboard/`.
- [Standalone Pixel Lab library](pixel-lab/README.md): `pixel-lab/`.
- [Validation suite](gauge/docs/validation.md): `gauge/src/gauge/validation/`.
- [Discovery research](research/README.md): `research/`.

## Verify

```sh
gauge/.venv/bin/python -m pytest gauge/tests -q
node --test pixel-lab/test/*.test.cjs gauge/dashboard/test/*.test.cjs
node gauge/dashboard/test/browser.cjs
```

The verification suite uses fixtures, not live inference. Local project state,
credentials, model outputs, databases, and dependency directories stay outside Git.

### Conversation agreement

CoQA and QuAC use JEV reference judgments to show the first semantic departure;
Multi-IF uses explicit instruction checks. The conversation tree includes full
request context, per-model branches, and separate benchmark metrics. Missing
outputs and uncertain judgments stay explicit.
See [conversation agreement trees](gauge/docs/validation.md#dashboard-and-persistence)
for saved-run judging and the separate backend JEV budget.
