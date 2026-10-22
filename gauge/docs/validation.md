# Public-data validation

Quality uses independent dataset labels and instruction checks. Pairwise JEV
agreement with OpenAI remains a separate measurement. MultiWOZ is excluded from
the quality suite: its historical answers require database/tool facts that the
request does not contain. Existing archived runs remain inspectable as replay tests.

## Run it

From the repository root, with the existing source, Fireworks, and JEV credentials:

```sh
make evals-all EVAL_PROFILE=smoke
make evals-coqa
make evals-quac
make evals-multiif
make evals-banking
make evals-squad
make evals-all
```

Targets prepare checksummed fixtures, check correlation offline, restart the idle
managed backend with validation dependencies, then dispatch. Restart refuses if
work is pending. Saved results persist. `EVAL_SOURCE_MODEL`, `EVAL_CONCURRENCY`
(default 4), and `EVAL_OUTPUT_DIR` override the corresponding settings.

Preparation alone does not invoke any models:

```sh
uv run --project gauge --extra validation python -m gauge.validation prepare \
  --profile full --dataset coqa --output out/coqa.manifest.json
```

The source defaults to `gpt-4.1-nano` with direct OpenAI credentials, or
`@openai-foundations/gpt-5.4-mini` through the configured gateway. The three
Fireworks candidates are Qwen3.8 Max, GPT-OSS-120B, and DeepSeek v4.1 Flash.
Source/candidate output caps are 1,024/2,048 tokens; only GPT-OSS receives low
reasoning effort. Incomplete outputs are unscored, never silent passes.

## Datasets and budgets

| Dataset | Smoke source calls | Full source calls | Independent metric |
| --- | ---: | ---: | --- |
| BANKING77 | 5 | 100 | Intent accuracy |
| SQuAD 2.0 | 6 | 100 | JSON validity, EM/F1; balanced answerability |
| CoQA | 2 × 4 = 8 | 50 × 4 = 200 | Multi-annotation answer EM/F1 |
| QuAC | 2 × 4 = 8 | 50 × 4 = 200 | Answer F1 and human equivalence |
| Multi-IF English subset | 2 × 3 = 6 | 50 × 3 = 150 | Strict prompt/instruction success |
| **Total** | **33** | **750** | **99 / 2,250 candidate calls** |

The first source request checks all three candidates and JEV before the remaining
budget is dispatched. Pairwise judgments have one bounded backfill; inference is
not rerun. Native benchmark metrics cost no model calls. QA tree judgments use
JEV in the backend: up to 1,600 additional decisions for the full suite (source
and three candidates, 400 CoQA/QuAC turns), with a separate 2,000-call budget.
The SDK still captures ordinary calls with no dataset annotations.

These are sampled, bounded evaluations, not official leaderboard submissions.
Selection is seeded per dataset, so individual and combined targets select the
same cases. Complete manifests record sources, hashes, IDs, prompts, reference
annotations, history policy, exclusions, and call budgets.

## Conversation protocol

CoQA and QuAC supply the passage in every request and use recorded assistant
history. The current reference answer never enters the request:

```text
u1                         -> e1
u1, a1, u2                 -> e2
u1, a1, u2, a2, u3         -> e3
```

The first four turns of each selected conversation are evaluated. CoQA includes
all human answer annotations, including duplicates, and uses the official
leave-one-annotation-out EM/F1 calculation. The initial domains are Wikipedia,
Gutenberg, and CNN. Conversations exceeding 14,000 passage characters are excluded.

QuAC includes article title, section title, and background for orientation, with
answers restricted to the supplied passage. It evaluates the answer-only subtask; yes/no and follow-up labels are not scored.
Unanswerable reference majorities/ties use `CANNOTANSWER`. Human-agreement F1 below
0.4 is excluded from quality averages and remains visible as unscored coverage.
Answer F1 and human equivalence remain benchmark metrics. Neither they nor CoQA
full-credit F1 determine tree agreement. JEV judges meaning against all human
annotations and the full request context, accepting supported paraphrases.
Questionable references, low confidence, and judge errors remain unresolved.

Multi-IF has instructions and checkers but no recorded assistant answers. Gauge
uses a **shared-source-history adaptation**: all candidates see the same previous
OpenAI replies. Those replies are context, never ground truth. If a source reply
fails or truncates, dependent later turns are marked undispatched/unscored.
Independent CoQA/QuAC turns continue after source failures.

The English-prompt Multi-IF subset can request outputs in other languages. It
includes only complete three-turn rows, prompts up to
4,000 characters, explicit deterministic checker arguments, and word requirements
up to 250. Two checkers requiring downloaded NLTK sentence models are excluded:
`length_constraints:number_sentences` and `change_case:capital_word_frequency`.
The pinned upstream strict checkers use language-detection seed 0; Thai tokenization
is unsupported and produces an unscored result. No runtime model downloads occur.
The manifest identifies this adaptation; it is not the full multilingual benchmark.

## Dashboard and persistence

The conversation tree shows the first confirmed semantic departure for CoQA/QuAC
and the first native instruction failure for Multi-IF. It includes agreement
through each turn, human reference annotations, evidence, complete request context,
outputs, JEV decisions, and separate benchmark metrics.
An earlier unscored turn prevents an exact first-failure claim. A later pass does
not erase an earlier failure. This is prefix evaluation, not autonomous rollouts.

Runs persist in the backend database. JSON reports and portable Pixel Lab HTML
are written beside the manifest. To register and judge a saved report without
rerunning source or candidate inference:

```sh
uv run --project gauge --extra validation python -m gauge.validation agreement out/evals/REPORT.json --judge
```

`--judge` spends backend JEV calls on unjudged or failed reference decisions;
completed judgments are reused. Omitting it refreshes saved results without paid
work. Multi-IF uses its explicit native checks and never enters the QA judge queue.
Viewing results never starts inference or judging. Every explicit resubmission
remains bounded by the backend reference budget.

## Code decomposition

Paths are under `gauge/src/gauge/`:

- `validation/sources.py`, `records.py`: pinned downloads, receipts, manifest contract.
- `validation/datasets.py`, `conversations.py`: raw format adapters and supported subsets.
- `validation/suite.py`: stable sampling and prompt/history contracts.
- `validation/dispatch.py`, `protocols.py`: source calls and independent audit ledger.
- `validation/prefixes.py`, `checks.py`: request-context and trace correlation audits.
- `validation/runner.py`, `drain.py`: orchestration, budgets, comparison/judge drain.
- `evaluation/scoring.py`, `instructions.py`: network-free native metrics/checkers.
- `_vendor/multiif.py`: pinned upstream evaluator with documented English-subset patch.
- `validation/agreement.py`: register benchmark criteria through the control API.
- `backend/agreement.py`: persist per-output scores and derive first-failure summaries.
- `validation/report.py`: portable HTML using the standalone Pixel Lab library.

History without provider IDs stays unresolved in automatic trace correlation.
Conversation membership in the benchmark tree comes from the dispatch manifest,
not guessed content similarity. No labels or grouping IDs are added to SDK calls.

## Sources and attribution

BANKING77 (Casanueva et al., 2020) is CC-BY-4.0; SQuAD 2.0 (Rajpurkar et al., 2018)
and QuAC (Choi et al., 2018) are CC-BY-SA-4.0. Selected CoQA domains (Reddy, Chen,
and Manning, 2019) use CC-BY-SA-4.0 / Apache-2.0. Multi-IF data (He et al., 2024)
is CC-BY-NC-2.0; its evaluator code is separately Apache-2.0. See
`_vendor/THIRD_PARTY.md` and `MULTIIF_LICENSE` for evaluator provenance.

Data is downloaded separately and is not bundled in the wheel. Preserve source
attributions and dataset license terms when sharing derived fixtures/results.
Pinned source URLs, revisions, and checksums are in `validation/sources.py` and
saved receipts. Corrupt cache entries fail closed rather than being replaced.
