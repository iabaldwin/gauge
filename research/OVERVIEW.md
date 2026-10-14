# Strategy

Find business directions worth attacking by reading what YC just funded, then testing each direction against DoorDash. A direction only survives if an internal source states the need.

## 1. Cluster the last four YC batches

Window is Winter 2026, Spring 2026, Summer 2026, and Fall 2026, pulled from the live YC directory. Fall is still filling, so it is counted and left out of trends.

YC's own industry tags are too coarse to use as the clusters. Each company is labeled from its YC description only:

- **Buyer**, who pays
- **Job**, the outcome
- **Domain**, a medium bucket used to find neighbors
- **Vertical**, one buyer, one job, one workflow

Labels that name the same market are merged. Labels stay apart when the buyer or the job differs. A pile is four or more companies. Consensus means a company in each completed batch. A surge means Summer holds more companies than Winter and Spring combined.

The shortlist is about ten piles and ten thin neighbors of those piles. The full taxonomy stays in `data/verticals.json`.

## 2. Test the need, fit, and remaining gap separately

Search Confluence and Slack in the language of the problem, read relevant page bodies and thread follow-ups, and check linked Jira work for current status. Record bounded searches and unavailable sources in `data/research/queries.json`.

Evaluate three separate questions:

- **Need:** does a source explicitly state a cost, miss, goal, or recurring failure?
- **Fit:** does it match this buyer and job directly, only an adjacent workflow, or neither in the evidence retrieved?
- **Remaining gap:** what do existing systems already cover, and what work remains? A deployed internal tool can validate demand while making an external product harder to sell.

An initial complaint followed by a fix is historical evidence, not an unresolved incident. A proposal is not a shipped result. No relevant search result means this pass did not establish support; it does not prove the need is absent.

## 3. Turn evidence into falsifiable business hypotheses

For supported needs, name the current alternative, a narrow proposed improvement, a measurable outcome, and the next interview or comparison that could disprove the hypothesis. Internal demand does not establish external willingness to pay. YC density and thin-neighbor labels describe this taxonomy, not market size or lack of competition.

The current synthesis is `out/doordash-discovery.md`; all 20 stable-ID dispositions are in `data/doordash-conclusions.json`. Source links and local snapshots are indexed in `data/research/sources.json`. The original first pass is preserved in `out/archive/doordash-discovery-first-pass.md`.
