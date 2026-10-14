# Merge rubric

Collapse near-duplicate vertical labels. Stay inside each `domain`. Do not move a company to another domain.

Merge companies when a founder would say they are competing for the same budget: the same buyer type and the same workflow, with different wording.

Do not merge when the buyer or the job is actually different. A hospital billing tool and a dental billing tool stay apart. Underwriting and claims stay apart. A sandbox sold to agent developers and a vertical agent sold to hotels stay apart. Infra for model labs and a product for end customers stay apart.

Singletons stay singletons. Do not invent a parent bucket to absorb leftovers. A merged group larger than 12 is too coarse; split it on the narrower workflow.

## Buyer and job strings

Reuse one buyer string across groups in the same domain when it is the same buyer type, so later scoring can see neighbors. Reuse one job string only when the outcome is the same.

Examples of one buyer string: carriers in any line are `insurers`; agencies and brokerages are `insurance brokers`; employers buying coverage are `employers`. Developers building agents are `agent developers`. Software teams buying coding tools are `engineering teams`.

Do not collapse different buyers into "companies" or "teams".

## Output

Write a single JSON object, no markdown:

```json
{"groups":[{"domain":"insurance-ops","vertical":"health insurance brokerage for employers","buyer":"employers","job":"place employee health coverage","slugs":["a","b"]}]}
```

- Every input slug appears in exactly one group.
- `domain` is copied from the input.
- `vertical` is a lowercase noun phrase, about 4–8 words, "{workflow} for {buyer}".
- No company names. No "AI-powered".
