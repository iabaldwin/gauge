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
- [Onboarding implementation plan](ONBOARD.md).
- [Discovery research](research/README.md): `research/`.

## Verify

```sh
gauge/.venv/bin/python -m pytest gauge/tests -q
node --test pixel-lab/test/*.test.cjs gauge/dashboard/test/*.test.cjs
node gauge/dashboard/test/browser.cjs
```

The verification suite uses fixtures, not live inference. Local project state,
credentials, model outputs, databases, and dependency directories stay outside Git.
