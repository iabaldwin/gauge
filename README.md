![Gauge — one import, many perspectives](gauge/docs/assets/gauge-header.svg)


# Gauge

**Same call. A wider view.**

[Package & quick start](gauge/README.md) · [Backend dashboard](gauge/dashboard/README.md) ·
[Pixel Lab library](pixel-lab/README.md) · [API scope](gauge/docs/api.md)

Gauge captures OpenAI calls without changing the response returned to an application.
A separate backend stores requests and compares model outputs. Pixel Lab provides
reusable, dependency-free comparison tables and request inspectors.

## Install

```sh
uv sync --project gauge --extra openai --extra dev
```

Use the existing OpenAI client with `gauge.wrap(client)`, or replace its import:

```python
from gauge.openai import OpenAI

client = OpenAI()
# Existing client calls stay the same.
```

Configure `GAUGE_ENDPOINT` and `GAUGE_API_KEY` in the application environment to
send captures to the backend. Comparisons and graders run in a separate process.

## Local backend and dashboard

With `FIREWORKS_API_KEY` set in the backend environment:

```sh
gauge/.venv/bin/python tools/gauge_backend.py start
gauge/.venv/bin/python tools/gauge_backend.py open
```

Open the sign-in link printed by the second command. The dashboard is served at
**http://127.0.0.1:8765/dashboard**. Captures and saved runs persist in a local
SQLite database; closing a test runner or browser does not stop the service.
See the [backend guide](gauge/dashboard/README.md) for saturation tests and lifecycle commands.

## Project layout

- [Gauge SDK and evaluation primitives](gauge/README.md): `gauge/src/gauge/`.
- Backend service: `gauge/src/gauge/backend/`; start directly with `python -m gauge.backend`.
- [Dashboard app](gauge/dashboard/README.md): `gauge/dashboard/`.
- [Standalone Pixel Lab library](pixel-lab/README.md): `pixel-lab/`.
- [Public-data validation](gauge/docs/validation.md): `gauge/src/gauge/validation/`.
- Local service management, benchmarks, and saturation runner: `tools/`.
- [Archived discovery research](research/README.md): `research/`.

Offline HTML reports and the live dashboard share Pixel Lab. Packaged builds
bundle the same UI assets rather than maintaining a second renderer.

## Verification

```sh
gauge/.venv/bin/python -m pytest gauge/tests -q
node --test pixel-lab/test/*.test.cjs gauge/dashboard/test/*.test.cjs
# For browser checks: cd pixel-lab && npm ci
node gauge/dashboard/test/browser.cjs
```

These checks use local fixtures and make no inference calls. Research snapshots,
model outputs, databases, credentials, and dependency directories stay outside Git.
