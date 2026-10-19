# Gauge terminal tools

The terminal is the first results interface. Install Gauge, swap the client, run
normal pytest tests, then inspect the saved run. No Gauge login or credentials.
The CLI discovers `.gauge/project.json` in the current project and starts the
separate service when needed. SDK capture does not start that service.

```sh
gauge project
gauge runs list
gauge requests
gauge requests RUN_ID --json
gauge inspect CALL_ID
gauge inspect CALL_ID --json
gauge runs show RUN_ID
gauge runs export RUN_ID --output out/run.json
gauge tui --color
gauge dashboard RUN_ID
```

`dashboard` opens the selected run in the browser. Omit the run ID to select the
latest. `--print-only` prints its ordinary URL without opening a browser. The
terminal and browser read the same captures, outputs, context, and judgments.
Original-only requests remain visible when no comparisons have run.

## Backend and evaluation configuration

```sh
gauge backend start
gauge backend status
gauge configure --budget 300
gauge compare RUN_ID --limit 100
gauge configure --models MODEL_A MODEL_B MODEL_C --budget 300 --automatic --sample-rate 0.5
gauge status
gauge judge RUN_ID --limit 30
gauge pause
gauge resume
gauge backend stop
```

Provider credentials are read by the backend process when it starts. With no
Fireworks key, originals remain inspectable and comparison dispatch reports the
missing configuration. After adding credentials to the environment, stop/start
the backend. Neither opening the console nor dashboard initiates paid work.

The comparison budget counts candidate jobs and survives backend restarts. Existing
jobs are not automatically repeated. Automatic mode evaluates newly captured work
up to the policy's budget; sampling is stable for a given request ID. Policy changes
require drained queues. Compare/JEV commands use provider inference when configured.

The TUI uses Pixel Lab colors, respects `NO_COLOR`, and offers `--color`/`--no-color`.
Keys: Enter inspects; `/` searches; `r` refreshes; `e` exports; `j` judges; `p`
pauses/resumes; `o` opens the selected run; `d` previews clearing one run; `D`
previews all; Escape cancels; `q` quits. Exiting leaves the backend running.

## Internal shared service

```sh
gauge connect https://YOUR-INTERNAL-GAUGE
gauge connect https://YOUR-INTERNAL-GAUGE --project-id PROJECT_ID
```

The first command registers the local project ID, uploads captures with their run
IDs, and connects future terminal/SDK work to that namespace. Run metadata and IDs
are retained. Reconnecting is idempotent. Selecting another project's ID is allowed
only when the local project has no captures. There are no user accounts or access
roles; namespaces segment data, not permissions.

Use `--connection PATH` for an explicit service connection file containing `url`
and optionally `project_id`. It contains no Gauge authentication token.

## Clearing results

```sh
gauge runs clear RUN_ID --dry-run
gauge runs clear RUN_ID
gauge runs clear --all --dry-run --json
gauge runs clear --all --yes --json
```

Interactive cleanup requires typing CLEAR. Noninteractive cleanup requires `--yes`.
The selected project is the boundary for preview, backup, and deletion. Queues must
be idle, the preview must still match, and surviving captures' context is protected.
A database backup is created before deletion. Exports and backups are retained.
