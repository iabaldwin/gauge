# Gauge terminal console

The optional operator console uses Pixel Lab's cream, navy, blue, yellow, green,
and orange palette. It connects to the same saved results and queues as the web
dashboard. It is separate from the drop-in SDK and adds no SDK runtime imports
or dependencies.

From the project root:

```sh
uv sync --project gauge --all-extras
gauge/.venv/bin/gauge tui --color
```

For an installed package, install `gauge[cli]`, then run `gauge tui`. The console
uses terminal colors by default unless `NO_COLOR` is set; `--color` explicitly
enables them and `--no-color` selects monochrome. It uses `.gauge/backend.json` by default; pass `--connection /path/to/backend.json`
to use another local backend. `python -m gauge` exposes the same commands.

## Terminal workspace

- The top strip shows live backend comparison progress, queued/active jobs,
  errors, and JEV work. These are backend process counters.
- The run picker selects persisted results. Model summaries show completed
  coverage, semantic agreement with its denominator, uncertain judgments,
  median candidate latency, and inference errors for the selected run.
- Narrow terminals use a compact layout with the result table retained; widen
  the terminal or use `gauge runs show RUN_ID` for the model summary.
- The result table lists captured exchanges and candidate models. Enter opens
  original/candidate outputs, captured input, and the complete stored judgment.
- Refresh polls about every 2.5 seconds. Disconnections show an error and retry;
  inspection and navigation remain available. Closing the console leaves the
  backend running.

Keys: `↑↓` navigate, `Tab` change focus, `Enter` select a run or inspect an output,
`/` find, `r` refresh, `e` export selected run, `j` enqueue at most 30 judgments,
`p` pause/resume comparison dispatch, `o` open the browser sign-in link,
`d` preview clearing the selected run, `D` preview clearing all saved runs,
`Esc` cancel a dialog, and `q` quit. Judging is a paid operation when triggered;
merely opening the console does not launch inference or judges. Pausing
comparison dispatch does not pause the independent judge workers.

## CLI utilities

```sh
gauge status
gauge status --json
gauge runs list
gauge runs show RUN_ID
gauge runs export RUN_ID --output out/saved-run.json
gauge judge RUN_ID --limit 30
gauge pause
gauge resume
gauge dashboard
```

`--json` is available on status, runs, judge, pause, and resume for scripts.
Noninteractive output uses Rich's terminal color detection and respects
`NO_COLOR`. Exports include run metadata, captured requests/responses, and
comparison/judgment results. They exclude connection credentials, use owner-only
permissions, and never overwrite an existing file.

## Clearing saved results

```sh
gauge runs clear RUN_ID --dry-run
gauge runs clear RUN_ID
# Explicit batch operation, after reviewing the preview:
gauge runs clear --all --dry-run --json
gauge runs clear --all --yes --json
```

The interactive CLI and TUI require typing `CLEAR` after showing the affected
runs, captures, comparisons, and judgments. Scripts must explicitly pass `--yes`.
The server requires an idle backend: no active run, comparison work, or judge
work. It rejects a changed preview and protects contexts referenced by surviving
captures. Clearing all saved runs does not select unassigned captures.

Before deleting anything, the backend saves an owner-only copy of the entire
SQLite database in a `backups/` directory next to the database. Selected run
metadata, capture associations, captures, and comparisons (including judgment
history) are then removed together in one transaction. Backups and independently
exported report files remain available. Restoring a backup requires stopping the
backend and replacing its database file.

The authenticated API is deliberately small: `POST /v1/runs/clear/preview`
accepts either `run_ids` or `all: true`; `POST /v1/runs/clear` requires the exact
`run_ids` and `fingerprint` from that preview. Read-only dashboard cookies cannot
authorize either endpoint.

## Code map

- `cli/client.py`: backend requests, pagination, export.
- `cli/views.py`: shared tables, progress, metrics, terminal text sanitization.
- `cli/commands.py`: command routing and plain/JSON output.
- `cli/tui.py`, `cli/dialogs.py`: interactive workspace and confirmation screens.
- `backend/maintenance.py`: preview, dependency checks, backup, atomic removal.

The interface uses [Textual](https://textual.textualize.io/guide/) and its
[headless testing API](https://textual.textualize.io/guide/testing/).
