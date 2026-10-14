"""Build local, linked audit artifacts from saved evidence; no network or model calls."""
import csv
import json
from collections import Counter
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/doordash-llm-audit"
OUT = ROOT / "out"
findings = []


def evidence(repo, path):
    file = DATA / "repos" / repo / "evidence" / (path.replace("/", "__") + ".json")
    return json.loads(file.read_text())


def add(id, name, priority, owner, interface, data, evaluation, blockers, sources):
    findings.append(dict(id=id, name=name, priority=priority, owner=owner,
                         interface=interface, data=data, evaluation=evaluation,
                         blockers=blockers, sources=[evidence(*s) for s in sources]))


add("supply", "Supply Bot intent and routing", "P0 · starter", "Logistics/Supply context; @yuxiangyang00 is latest classifier contributor, not a verified owner",
    "AsyncOpenAI → Chat Completions; direct or Portkey gateway. Fits current capture wrapper.",
    "24 committed labeled cases; 7 intents. Extracted to starter-fixtures/supply-intent.jsonl. Tests also contain edge cases.",
    "Parse JSON; intent exact match and macro-F1; apply the existing routing map and confidence fallback, then score service + handler. Measure malformed output, latency, thought/answer tokens. Freeze prompt dates and few-shot setting.",
    "Existing test gates are 80% intent / 95% routing / 80% full match; these are regression gates, not a statistically powered replacement decision. Need rendered prompt, routing postprocessor and a task Grader in Gauge. Fresh production holdout still needed.",
    [("ai-labs-backbone", "src/backbone/logistics/supply/services/supply_intent_classifier.py"), ("ai-labs-backbone", "tests/logistics/supply/evals/test_supply_intent_classification_evals.py"), ("ai-labs-backbone", "src/backbone/logistics/supply/config/intent_routing.yaml")])

add("reranker", "Zesty query–document relevance", "P0 · pilot", "Zesty/search workload; current owner unverified",
    "AsyncOpenAI directly against Fireworks; binary yes/no output. Fits current capture wrapper.",
    "Named zesty-qir_2025-10-03_split_gradated dataset with separate train/test folders; actual dataset not downloaded.",
    "Hold query, document and coordinates fixed. Score precision/recall/F1 against labels; add grouped NDCG/Recall@K only where full candidate lists exist. Report invalid labels separately from negative predictions. Include currently configured model as incumbent.",
    "Dataset access required. Existing parser falls back to substring 'yes', which can convert malformed answers into valid scores. The inspected eval defaults to a different Modal model, so explicitly select each candidate. Confirm harness imports before running.",
    [("asgard", "services/zesty-service/ml-api/rexai/models/rerank/slm_fireworks_reranker_v3.py"), ("asgard", "services/zesty-service/ml-api/rexai/evals/eval_reranking.py")])

add("domain", "Vera / Datadogs domain classification", "P1 · labeled", "@jashdd is latest classifier contributor; owner confirmation outstanding",
    "OpenAI-shaped classifier with existing llm/jev/hybrid backend selection; text classification slice.",
    "Approved active questions loaded from DB or --questions-json; frozen stratified split and repeat support. September protocol reports 1,237 approved examples; current count not verified.",
    "Domain exact match, gold-in-pin, per-domain recall, routed accuracy at matched menu/abstention rates, calibration, paired wins/losses and repeat variance. Use the same domain descriptions, glossary and boundary rules across models.",
    "Need frozen questions plus domain/glossary state. Do not replace the human domain label with Terra's answer. Report hybrid fallback separately. Existing protocol is stronger than literal output equality.",
    [("ai-labs-backyard", "src/backyard/datadogs/domain_classifier.py"), ("ai-labs-backyard", "scripts/datadogs/rerun_domain_classifier_baseline.py"), ("ai-labs-backyard", "docs/converse-research/2026-09-24-jev-domain-classifier-offline.md")])

add("merchant-routing", "Merchant orchestrator routing", "P1 · fixture", "Merchant orchestration context; @zhenghu-dd is last harness contributor (2025), ownership may have changed",
    "Existing harness calls a service endpoint; model-level routing extraction is still needed for Gauge.",
    "72 committed rows, all with empty prev_turn_id; 5 distinct agent labels, including rows allowing multiple agents. Extracted with accepted sets intact.",
    "First-routed-agent membership in accepted set; score final responder separately. Per-route confusion matrix, escalation and malformed-route rates. Preserve ramen normalization. Avoid exact string equality over comma-separated labels.",
    "The endpoint harness may run a whole agent workflow. A single model comparison needs the actual router prompt/tool schema and fixed context, or an offline harness adapter. Do not treat the saved CSV alone as a complete replay payload.",
    [("ai-labs-backbone", "production/merchant/orchestration_agent/single_turn_eval/single_turn_test_cases.csv"), ("ai-labs-backbone", "production/merchant/orchestration_agent/single_turn_eval/run_routing_evaluation_parallel.py")])

add("notification", "NV notification copy generation", "P1 · business", "New Verticals notification workload; @kaiwangsea is latest client contributor",
    "Native Portkey with with_options(config, metadata, retry), structured JSON; current Gauge wrapper does not support this client.",
    "Existing generation pipeline, deterministic validation and second-stage LLM judge; no independent labeled holdout downloaded.",
    "Reuse placeholder preservation, length/style caps and alcohol-disclaimer checks. Score relevance and wording with a calibrated independent judge; compare all valid variants, failure rate and tokens. Audit whether the existing generator/judge share biases.",
    "Portkey adapter or backend payload import required. Needs frozen campaign/store inputs and approved rubric. README's batch volume estimates are not current traffic/spend measurements.",
    [("dd-models", "models/consumer/new_verticals/nv_notification/genai_copy/llm_client.py"), ("dd-models", "models/consumer/new_verticals/nv_notification/genai_copy/README.md")])

add("catalog-size", "Catalog size and pack extraction", "P1 · structured", "NVML Catalog (README); @sanjy0t is latest size-evaluator contributor",
    "Native AsyncPortkey. Current client wrapper unsupported; text extraction can be a backend comparison after inputs are frozen.",
    "Existing size_match scorer and CSV runner paths; underlying labeled dataset not validated in this audit.",
    "Unit-normalized size/pack correctness, JSON/schema validity, unknown/abstain rate, per-unit-family errors. Add tests for pack multiplication and dimensions before adopting the scorer.",
    "Scorer currently treats mass as volume using density≈1 and has a 15% default tolerance. Audit those semantics with catalog owners; copied grading code is not automatically a trustworthy oracle. MCP/grounding variants require fixed retrieved evidence.",
    [("dd-catalog-ml", "README.md"), ("dd-catalog-ml", "dd_catalog_ml/shadow/size_inference/client.py"), ("dd-catalog-ml", "dd_catalog_ml/shadow/size_inference/eval.py")])

add("guardrail", "DashBuddy failure-category triage", "P1 · classification", "DashBuddy context; @nathanlindd is latest classifier contributor",
    "AsyncOpenAI via Portkey gateway; JSON failure-category classifier. This is failure triage, not a generic input-safety detector.",
    "Classifier taxonomy plus golden-eval documentation. Human-maintained golden set is described; actual records not retrieved.",
    "Per-category precision/recall; false-positive-vs-real-failure confusion; valid taxonomy output; compare against independently adjudicated failure labels. Retain conversation and applicable KB/system facts.",
    "Need actual labeled failure examples and taxonomy version. Golden-eval design documentation alone does not establish the size or health of the deployed dataset.",
    [("ai-labs-backbone", "src/backbone/dashbuddy_chat_analyzer/guardrail_classifier.py"), ("ai-labs-backbone", "src/backbone/dashbuddy_chat_analyzer/docs/golden-eval/README.md")])

add("risk-labels", "Risk chargeback backtagging", "P1 · labeled", "Risk context; @zhifeng-doordash is latest label-scorer contributor",
    "Batch label evaluation; Risk also has a multi-model OpenAI/Portkey client. Specific backtagging generation route needs separate verification.",
    "evaluate_labels.py joins model batch CSVs to golden labels, with strict, grouped and generalized label mappings.",
    "Use strict label accuracy as primary; publish grouped scores separately, per-class recall and coverage/missing-output rate. For decision-sensitive classes, use owner-defined asymmetric error costs.",
    "Golden labels and frozen model-visible evidence are not downloaded. Prevent reviewer decisions/future evidence leaking into inputs. A label scorer does not establish end-to-end task replayability.",
    [("risk-ai", "projects/cb_backtagging/evaluate_labels.py"), ("risk-ai", "projects/llm_as_a_judge/clients/multi_model_client.py")])

add("risk-robocop", "Robocop V2 defect decisions", "P2 · harness", "@doordash/fraud and @doordash/fraud-platform (Pedregal CODEOWNERS)",
    "Agentic evidence-gathering workflow; requires existing replay harness or frozen fact snapshots.",
    "Committed golden-set manifests and human gut-review decisions. README describes point-in-time WOD 500 and M&I 364 balanced cases; record counts not independently recounted.",
    "Decision agreement with human review, per-defect false approvals/denials, evidence-grounding checks, tool costs, timeouts and abstentions. Score only claim-time evidence, not later hydrated facts.",
    "Not portable through Gauge's text-only Experiment without a replay adapter. Need point-in-time snapshot access and complete defect context; manifest gaps remain part of the denominator.",
    [("pedregal", "CODEOWNERS"), ("pedregal", "services/risk_ai/dev/robocop_v2/golden_sets/README.md"), ("pedregal", "services/risk_ai/components/clients/portkey_client.py")])

add("voice-rewrite", "Voice/consumer query rewriting", "P1 · frozen context", "Voice/consumer search context; @danfischettidd is latest inspected eval-README contributor (2025)",
    "Text rewrite subtask inside broader voice/search stack; end-to-end audio requires a different adapter.",
    "Offline CSV query corpus, cached-profile paths, V1/V2 comparison and rewrite rubrics are documented.",
    "Intent/constraint preservation, unsupported personalization, schema validity and downstream retrieval success on fixed candidates. Grade rewrites semantically rather than literal equality.",
    "Freeze consumer profile and retrieval state. 'Offline' examples still read Snowflake, so verify data dependencies; no actual profiles or result files were pulled. Current owner needs confirmation.",
    [("voice-ai-service", "src/v2vburg/m1/demo/query_rewrite/offline_eval/README.md"), ("voice-ai-service", "src/v2vburg/m1/evals/README.md")])

add("support", "Support automation / Iris", "P2 · harness", "@doordash/support-automation (Pedregal CODEOWNERS); @davidborrellidd is inspected eval-doc contributor",
    "Existing conversation simulators and multiple independently owned harnesses; tools and voice/chat context matter.",
    "src/evals, SOP AutoGen's fork, and independent src/simulations workbench. Evals Service project is documented; no live traces queried.",
    "Resolution, correct escalation, SOP adherence, grounded response and session outcome. Stratify chat/voice and sub-agent; use scripted user/environment fixtures for paired trials.",
    "Capture alone cannot reproduce stateful tools. Avoid evaluation self-traces and bot welcome messages when sampling customer input. Historical volume/connection-health notes were not revalidated and are not current findings.",
    [("support-automation", "src/evals/README.md"), ("support-automation", "knowledge/evals-service-saaa-project.md"), ("pedregal", "CODEOWNERS")])

add("consumer-mcp", "Consumer MCP discovery/cart scenarios", "P2 · existing evals", "Consumer MCP harness owners; precise current team unresolved in inspected ownership file",
    "Existing offline scripted replay and LLM simulation using Cybertron/OpenAI-compatible providers.",
    "Repository scenarios, fresh per-model/trial fixture state, saved judges and baseline hashes.",
    "Reuse scenario pass, task success, tool-choice quality, grounding, error clarity and injection-resistance metrics. Run each arm against identical fixture state and trial IDs.",
    "Reuse the task harness rather than flattening tool trajectories into Gauge text cases. Live replay is a separate mode and should not be mixed into the offline comparison.",
    [("pedregal", "services/consumer_mcp/evals/README.md")])

add("samwise", "Samwise incident investigation", "P2 · existing evals", "@doordash/reliability-platform (Pedregal CODEOWNERS)",
    "Offline past-incident harness compares two or more Samwise variants; service/MCP state is part of execution.",
    "Scenario suites, variant configuration, judge options and existing offline experiment harness.",
    "Reuse incident-specific rubric, diagnosis/evidence quality, tool usage, latency and cost. Compare task outcomes, not just next-turn textual agreement.",
    "Needs configured Samwise variants, scenario dependencies and judge access. Current Gauge cannot substitute for that runtime.",
    [("pedregal", "services/samwise_agent/eval/README.md"), ("pedregal", "CODEOWNERS")])

add("cpg", "Ask Data CPG quantitative answers", "P2 · frozen gold", "Ask Data AI platform channel in README; specific domain owner unresolved",
    "Existing agent evaluation engine with domain retrieval/SQL context.",
    "Question CSV, expected-answer rubrics, SQL-to-question index and frozen result CSVs. Ground-truth package documentation inspected; dataset not executed.",
    "Correct method/table/filter/grain plus numeric agreement against frozen results; preserve documented ±5% tolerance and hard failure rules. Use snapshot-aligned context; compare SQL result semantics rather than exact SQL strings.",
    "Model-only replacement needs frozen retrieval/schema/results or the existing harness. Judge reads expected-answer rubric text, not result CSVs directly; mismatched rubrics can silently corrupt scores.",
    [("ask-data-ai-service", "application/ai_service/agents/ask_ads_insights_cpg/evaluation/ground_truth/category_platform_growth/README.md"), ("ask-data-ai-service", "README.md")])

add("ranger", "Ranger catalog research / grounded extraction", "P2 · existing evals", "NVML Catalog; current Ranger owner unresolved",
    "Existing MCP/open-source search comparisons, including Qwen/Modal configurations.",
    "Reusable comparison, grouped metrics, failure-analysis and cost-accounting modules.",
    "Reuse field correctness, source attribution and grouped failure analysis. Freeze search evidence for model-only tests; measure search-provider and inference costs separately.",
    "Current Gauge text adapter cannot replay search/MCP itself. Resumed runs need all invocation logs or actual billing; final invocation metadata undercounts full-run costs.",
    [("dd-catalog-ml", "dd_catalog_ml/ranger/v2/mcp_integration_evals/README.md")])

add("websites", "AI Websites generated content and layout", "P3 · separate modality", "SMB Direct Commerce Platform; README names Luc Danziger (Product), Rodrigo de Benito Sanz (Design), engineering roster; current status unverified",
    "Multi-stage website generation and quality evaluation described in product/eval repository documentation.",
    "Evaluation table/output paths for accessibility, buttons, layout and navigation; no executable model harness verified here.",
    "Copy/SEO correctness can be a text slice; accessibility, navigation and visual layout need rendered-page/browser assertions and visual evaluation.",
    "Documentation evidence only for this audit. Do not imply a client import swap can evaluate a rendered website or that historical evaluation CSVs are a maintained golden set.",
    [("ai-websites-evals", "README.md")])


def build():
    supplemental = DATA / "new-workloads.json"
    if supplemental.exists():
        additions = json.loads(supplemental.read_text())
        if isinstance(additions, dict):
            additions = additions.get("findings", additions.get("workloads", []))
        existing_ids = {item["id"] for item in findings}
        findings.extend(item for item in additions if item["id"] not in existing_ids)
    review_path = DATA / "pilot-review.json"
    if review_path.exists():
        reviews = {r["id"]: r for r in json.loads(review_path.read_text())["reviews"]}
        for item in findings:
            if item["id"] in reviews:
                item["reviewed_readiness"] = reviews[item["id"]]["status"]
    inventory = json.loads((DATA / "repositories.json").read_text())
    repos = inventory["repositories"]
    queries, hits = [], {}
    for path in sorted((DATA / "search").glob("*.json")):
        doc = json.loads(path.read_text())
        pages = doc.get("pages", [])
        queries.append({"query": doc["query"], "pages": len(pages), "received": len(doc["items"]),
                        "reported_total": pages[-1]["total_count"] if pages else None,
                        "capped": doc.get("capped", False),
                        "complete_within_cap": doc.get("complete_within_cap", False),
                        "index_drift": len({p["total_count"] for p in pages}) > 1,
                        "incomplete_results": any(p.get("incomplete_results") for p in pages)})
        for item in doc["items"]:
            key = item["repository"]["full_name"], item["path"]
            entry = hits.setdefault(key, {"repository": key[0], "path": key[1], "url": item["html_url"], "queries": []})
            entry["queries"].append(doc["query"])
    counts = Counter(repo for repo, _ in hits)
    evidence_files = [json.loads(p.read_text()) for p in (DATA / "repos").glob("*/evidence/*.json")]
    pinned = {"doordash/" + p.parent.name for p in (DATA / "repos").glob("*/head.json")}
    evidence_repos = {e["repository"] for e in evidence_files}
    trees = [{"repo": p.parent.name, "truncated": json.loads(p.read_text())["truncated"]} for p in (DATA / "repos").glob("*/tree.json")]
    coverage = {"as_of": "2026-10-12 America/Los_Angeles", "visible_repositories": len(repos),
                "inventory_pages": inventory["pages"], "active_nonfork_repositories": sum(not r["archived"] and not r["fork"] for r in repos),
                "queries": queries, "distinct_matching_files": len(hits), "repositories_with_search_hits": len(counts),
                "pinned_repositories": len(pinned), "saved_evidence_files": len(evidence_files),
                "curated_workloads": len(findings), "tree_coverage": trees,
                "limits": ["GitHub index only; query matches are not proof of live usage.",
                           "Search page totals drifted during retrieval; deduplication cannot recover skipped hits.",
                           "Queries above 1000 remain capped; narrowed runtime queries supplement rather than prove complete recovery.",
                           "Code search was throttled (HTTP 403); saved pages retained and resumable.",
                           "Pedregal and Asgard whole-repo trees were truncated; seven selected subtrees were fetched completely.",
                           "All visible repo metadata inventoried; source bodies inspected selectively, not all 3283 repositories.",
                           "No live usage, spend, deployment state, dataset health, or model performance measured."]}
    (DATA / "coverage.json").write_text(json.dumps(coverage, indent=2))
    (DATA / "findings.json").write_text(json.dumps(findings, indent=2))
    (DATA / "source-manifest.json").write_text(json.dumps(evidence_files, indent=2))
    (DATA / "search-hits.json").write_text(json.dumps(list(hits.values()), indent=2))
    with (DATA / "repository-inventory.csv").open("w") as file:
        columns = ["repository", "archived", "fork", "language", "pushed_at", "matching_files", "evidence", "url"]
        writer = csv.DictWriter(file, fieldnames=columns); writer.writeheader()
        for r in repos:
            name = r["full_name"]
            writer.writerow(dict(repository=name, archived=r["archived"], fork=r["fork"], language=r["language"], pushed_at=r["pushed_at"],
                                 matching_files=counts[name], evidence="pinned source/doc evidence" if name in evidence_repos else "search hits only" if counts[name] else "not established by these queries", url=r["html_url"]))
    narrative = f"""# DoorDash LLM usage and Gauge evaluation audit

**Start with actual task labels and incumbent prompts. Use Terra as another comparison arm, not as ground truth.** The best initial slices are Supply Bot intent classification and Zesty query–document relevance, followed by Vera domain routing, merchant routing, and NV notification copy.

Audit date: 12 October 2026 (America/Los_Angeles). Read-only GitHub investigation; no model calls, production jobs, messages, deployments or upstream tests executed.

## Coverage and what “exhaustive” means here

Enumerated **{len(repos):,} visible repositories** over {inventory['pages']} pages ({coverage['active_nonfork_repositories']:,} active non-forks). Ran **{len(queries)} search families**, saved **{len(hits):,} distinct matching paths across {len(counts)} repositories**, pinned **{len(pinned)} repositories**, and saved **{len(evidence_files)} source/document files**. The curated matrix below covers **{len(findings)} workloads**.

This is an organization-wide discovery pass with targeted source review, **not an exhaustive proof of every LLM caller**. The org metadata reports more repositories than this account can enumerate. Inaccessible repositories, unindexed/generated code, other branches and runtime-only configuration are outside the evidence. {sum(q['capped'] for q in queries)} broad queries exceed GitHub's 1,000-result cap; query totals changed during pagination, and search was throttled. Narrow queries improve coverage but do not erase those gaps. Whole-repo Pedregal/Asgard trees were truncated; seven selected subtrees were fetched separately without truncation.

Keyword hits are not adoption counts. Dependencies, tutorials, evaluator self-calls, vendored implementations, snapshots, forks and duplicated monorepo paths are all present. No source-only audit can establish current production volume, spend or deployment health. Missing hits mean **not established**, never “not using LLMs.” The [full repo inventory](../data/doordash-llm-audit/repository-inventory.csv), [query/coverage ledger](../data/doordash-llm-audit/coverage.json), [path inventory](../data/doordash-llm-audit/search-hits.json) and [pinned source manifest](../data/doordash-llm-audit/source-manifest.json) retain these distinctions.

## Findings that change the Gauge plan

1. **Single-turn tasks are real and useful.** Intent classification, routing, relevance judgments, failure taxonomy, copy validation and structured attribute extraction offer a smaller first scope than whole-agent replay.
2. **There are two different Portkey integration cases.** Native OpenAI clients with a Portkey base URL are compatible with Gauge's current client class checks; native Portkey/AsyncPortkey clients need another adapter. Similar method names do not make them interchangeable.
3. **Several good evals already have labels or deterministic checks.** Terra agreement should remain a secondary metric. Compare incumbent + Terra + at least two candidates against the same independent oracle where available.
4. **Existing platform work overlaps heavily.** Model Switch Advisor already handles replay/prompt tuning and load testing; Evals SDK supports datasets and batch evaluators. Gauge's opportunity is discovery, easy capture, input normalization, coverage-aware comparisons and calibrated task graders—not rebuilding those systems blindly.
5. **The grader itself needs an audit.** Catalog's density approximation/tolerance, Zesty's permissive yes/no parser, grouped risk-label mappings, and CPG rubric-to-snapshot alignment can all change conclusions independently of model quality.
6. **A client swap is collection, not complete replay.** Gauge still lacks durable export/automatic dispatch, a native Portkey adapter and general tool/multimodal/stateful replay. Those gaps are explicit below.

## Who uses it and what to evaluate

Owners from CODEOWNERS are identified as such. Named individual handles are last contributors to the specific inspected path; they are contact leads, **not verified current owners**. Workload names derived from source paths are contextual, not an org-directory lookup.

"""
    for f in findings:
        narrative += f"### {f['priority']} — {f['name']}\n\n**Who:** {f['owner']}.\n\n**Interface:** {f['interface']}\n\n**Existing data/evals:** {f['data']}\n\n**Proposed comparison:** {f['evaluation']}\n\n**What remains:** {f['blockers']}\n\n"
        if f.get("reviewed_readiness"):
            narrative += f"**Readiness review:** {f['reviewed_readiness'].replace('_', ' ')}.\n\n"
        narrative += "Evidence: " + ", ".join(f"[{s['repository']}/{s['path']}]({s['url']})" for s in f["sources"]) + ".\n\n"
    platform = [evidence("dd-models", "models/platform/ml_platform/genai/model_switch_advisor/README.md"), evidence("genai-docs", "evals/evals-sdk/19-datasets.md"), evidence("genai-docs", "evals/evals-sdk/17-batch-evaluator-runs.md"), evidence("flux-agent-hub", "CODEOWNERS")]
    narrative += """## Existing infrastructure to reuse

Model Switch Advisor documents production-log replay, calibrated evaluators, tune/holdout separation, prompt optimization and capacity/cost experiments. The `mlp` Flux playbooks are owned by **@doordash/ml-platform**; **@pranavkrishnan99** is the latest inspected Model Switch Advisor README contributor. These are evidence of an existing implementation, not evidence its jobs were healthy or run in this audit.

Evals SDK already supports dataset items with expected outputs and trace lineage plus batch evaluation. Its dataset docs explicitly note that Langfuse-style dataset-run linking is absent; integration must account for that instead of assuming API parity. Consumer MCP, Samwise, support automation, Ranger and Ask Data have richer task harnesses worth adapting.

""" + "Evidence: " + ", ".join(f"[{s['path']}]({s['url']})" for s in platform) + ".\n\n"
    narrative += """## Concrete first evaluation batch

Prepared **96 unique regression inputs** locally: [24 Supply Bot cases](../data/doordash-llm-audit/starter-fixtures/supply-intent.jsonl) and [72 merchant-routing cases](../data/doordash-llm-audit/starter-fixtures/merchant-routing.jsonl). [Manifest with hashes](../data/doordash-llm-audit/starter-fixtures/manifest.json). Expected labels are stored separately from inputs and must only be passed to scorers. All merchant rows have no previous-turn dependency; five rows accept more than one route.

These are a smoke/regression starting point, **not a held-out benchmark and not already executed**. Supply needs prompt rendering with a frozen clock plus existing confidence/routing postprocessing. Merchant needs the actual router prompt/tool representation, or its established service harness. Neither is made faithful by replacing the task with a generic category prompt.

Proposed model arms: the workload's incumbent, Terra, Qwen and GPT-OSS; deduplicate any identical arm. Keep prompt/schema/context fixed for the first experiment; test prompt adaptation separately on a development split. For 96 cases and four distinct arms, the first pass is **384 model calls**, excluding probes, dataset generation, judges, retries and multi-turn harness activity. Three repeats would be **1,152** calls under the same exclusions. These are planning counts, not calls made.

For a replacement decision, add a frozen, independently labeled production holdout stratified by label, language, input length, difficult/rare cases and failure mode. Select its size from the allowed regression and observed class frequencies; a 24-case suite cannot justify a 1-percentage-point non-inferiority claim. Preserve train/dev/test isolation. Synthetic examples expand edge coverage and require label review; they do not replace the production holdout.

Every report should include all attempted calls, parse/schema success, missing/incomplete outputs, task accuracy or task-specific score, per-slice results, paired wins/losses with uncertainty, latency distribution, reported thought/answer/total tokens and actual available cost data. Do not award a pass on blank outputs, drop failures from coverage, or infer token counts that a provider does not report. Review a blinded sample of both disagreements and agreements to catch shared errors.

## How to finish a genuinely exhaustive census

1. Resolve capped/drifting search partitions by repository and language; retain a manifest of what was searched, with stable source snapshots for verified callers.
2. Inspect unmatched active repositories using language manifests and gateway/raw-HTTP patterns; include Kotlin/TypeScript/native Anthropic, embeddings, multimodal and batch paths explicitly. The current 3,283-row inventory is the tracking ledger, not evidence each repo's source was inspected.
3. Resolve ownership through service catalog/CODEOWNERS and team confirmation. GitHub access teams or recent authors alone are not sufficient ownership proof.
4. Reconcile verified source workloads with gateway/evals project metadata and aggregate traffic to identify live/high-value adopters. This audit did not query live telemetry or equate repository push dates with usage.
5. Validate dataset versions, label provenance, task semantics and execution prerequisites with owners. Reuse existing harnesses; add Gauge adapters only where a concrete pilot demands them.

The next implementation should be a **Supply/Zesty pilot plus native Portkey and dataset/trace ingestion**, with automatic grader proposals introduced only after calibration against existing labels. Full workflow replay can stay behind task-specific harness adapters.
"""
    (OUT / "doordash-llm-audit.md").write_text(narrative)
    e = lambda x: escape(str(x), quote=True)
    cards = "".join(f'<div class="card"><strong>{n:,}</strong><span>{label}</span></div>' for n,label in [(len(repos),"visible repositories"),(len(hits),"matching file paths"),(len(counts),"repos with search hits"),(len(findings),"curated workloads")])
    rows = ""
    for f in findings:
        links = " ".join(f'<a href="{e(s["url"])}" target="_blank" rel="noreferrer">Source {i+1}</a>' for i,s in enumerate(f['sources']))
        search = " ".join(str(f[k]) for k in ['name','priority','owner','interface','data','evaluation','blockers']).lower()
        rows += f'<tr data-search="{e(search)}"><td><span class="pill">{e(f["priority"])}</span><h3>{e(f["name"])}</h3><p>{e(f["owner"])}</p>{links}</td><td>{e(f["interface"])}<details><summary>Existing data</summary><p>{e(f["data"])}</p></details></td><td>{e(f["evaluation"])}</td><td>{e(f["blockers"])}</td></tr>'
    queryrows = "".join(f'<tr><td>{e(q["query"])}</td><td>{q["received"]:,}</td><td>{e(q["reported_total"])}</td><td>{"Capped" if q["capped"] else "Incomplete" if not q["complete_within_cap"] else "Pages collected"}{" · index drift" if q["index_drift"] else ""}</td></tr>' for q in queries)
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Gauge · DoorDash workload audit</title><style>
    :root{{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202820;background:#fafbf8}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1600px;margin:auto;padding:36px 44px}}nav{{display:flex;justify-content:space-between;border-bottom:1px solid #dde2d8;padding-bottom:20px}}a{{color:#326344;text-underline-offset:3px}}h1{{font-size:46px;letter-spacing:-2px;font-weight:500;margin:40px 0 14px}}h2{{font-size:22px;font-weight:550}}h3{{font-size:16px;line-height:1.4;margin:12px 0}}.lede{{color:#5f695e;max-width:850px;line-height:1.7}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:30px 0}}.card{{border:1px solid #dbe1d5;background:#f0f4ea;border-radius:12px;padding:22px}}.card strong{{font-size:34px;font-weight:500;display:block}}.card span{{font-size:12px;color:#65715f}}.notice{{padding:18px 22px;background:#fff3de;border:1px solid #e9dbbf;border-radius:10px;line-height:1.6;font-size:13px}}.toolbar{{display:flex;justify-content:space-between;align-items:center;margin-top:32px}}input{{padding:12px 15px;border:1px solid #dbe1d5;border-radius:7px;width:340px;font:inherit;font-size:13px;background:white}}.table-shell{{overflow:auto;border:1px solid #dde2d8;border-radius:12px;background:white}}table{{border-collapse:collapse;width:100%;text-align:left;font-size:13px;line-height:1.65}}th{{background:#eff3e9;font-weight:550;padding:17px}}td{{padding:20px 17px;border-top:1px solid #e5e9e0;vertical-align:top;min-width:230px;width:25%}}td p{{color:#64705f}}td a{{font-size:11px;margin-right:9px}}.pill{{font-size:10px;background:#edf3e3;border-radius:4px;padding:5px 7px;color:#56723e}}details{{margin-top:14px}}summary{{cursor:pointer;color:#536e44;font-size:12px}}section{{margin-top:30px}}.plan{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.plan>div{{background:white;border:1px solid #dde2d8;border-radius:12px;padding:24px;line-height:1.7;font-size:14px}}footer{{padding:25px 0;color:#74806d;font-size:12px}}button{{border:1px solid #dbe1d5;padding:8px 12px;border-radius:7px;background:white;cursor:pointer}}[hidden]{{display:none!important}}@media(max-width:800px){{main{{padding:22px}}h1{{font-size:34px}}.cards,.plan{{grid-template-columns:1fr 1fr}}.toolbar{{display:block}}input{{width:100%;margin-bottom:18px}}}}@media print{{input,button{{display:none}}main{{padding:0}}.table-shell{{overflow:visible}}td{{min-width:0;font-size:10px}}tr[hidden]{{display:table-row!important}}}}
    </style></head><body><main><nav><b>gauge / research</b><span>DoorDash · 12 October 2026 <button id="print">Print / PDF</button></span></nav>
    <h1>Find the workloads.<br>Evaluate the actual task.</h1><p class="lede">An organization-wide GitHub discovery pass: who is calling models, which evaluation assets already exist, and where Gauge can deliver a useful comparison first.</p>{cards}
    <div class="notice"><b>Coverage is explicit.</b> These are search signals and selectively inspected sources, not counts of production adopters. Capped queries, index drift, inaccessible repositories and truncated monorepo trees prevent an exhaustive claim. No model benchmarks or live traffic measurements were run.</div>
    <section class="plan"><div><h2>Start with bounded tasks</h2>Supply intent classification and Zesty relevance are the first pilots. Domain routing, merchant routing and notification copy follow. Score against independent labels or deterministic checks; keep Terra agreement as a secondary signal.</div><div><h2>Reuse what already exists</h2>Model Switch Advisor, Evals SDK and team-owned harnesses already cover much of replay and grading. Gauge should make collection and comparisons easier. Native Portkey, durable export and task adapters remain implementation gaps.</div></section>
    <div class="toolbar"><h2>Workload &amp; evaluation matrix</h2><label><span class="sr-only">Search </span><input id="search" placeholder="Filter by team, task, API, or readiness…"></label></div><p id="count" aria-live="polite">{len(findings)} workloads</p>
    <div class="table-shell"><table id="workloads"><thead><tr><th>Workload / contact evidence</th><th>Interface &amp; data</th><th>What to measure</th><th>Readiness / remaining work</th></tr></thead><tbody>{rows}</tbody></table></div>
    <section class="plan"><div><h2>96 starter inputs prepared</h2>24 labeled supply-intent cases and 72 merchant-routing rows. Labels stay scorer-only; multiple acceptable routes are preserved. These are regression fixtures, not a held-out production benchmark.<p><a href="../data/doordash-llm-audit/starter-fixtures/manifest.json">Fixture manifest</a></p></div><div><h2>One fair comparison</h2>Incumbent + Terra + two candidates, with fixed prompts and context. Track task score, coverage, malformed/partial answers, paired differences, latency and reported thought/answer tokens. Validate the grader before trusting the winner.</div></section>
    <section><h2>Evidence &amp; coverage</h2><p><a href="doordash-llm-audit.md">Full written audit</a> · <a href="../data/doordash-llm-audit/repository-inventory.csv">All {len(repos):,} visible repositories</a> · <a href="../data/doordash-llm-audit/source-manifest.json">Pinned sources</a> · <a href="../data/doordash-llm-audit/coverage.json">Coverage ledger</a></p><details><summary>Inspect search coverage</summary><div class="table-shell"><table><thead><tr><th>Query</th><th>Rows collected</th><th>Last reported total</th><th>Status</th></tr></thead><tbody>{queryrows}</tbody></table></div></details></section>
    <footer>Internal research · Local artifact · GitHub source access required · No external assets</footer></main><script>(()=>{{const rows=[...document.querySelectorAll('#workloads tbody tr')];const input=document.getElementById('search');input.addEventListener('input',()=>{{let n=0;for(const row of rows){{row.hidden=!row.dataset.search.includes(input.value.toLowerCase().trim());if(!row.hidden)n++;}}document.getElementById('count').textContent=n+' of '+rows.length+' workloads';}});document.getElementById('print').addEventListener('click',()=>window.print());}})();</script></body></html>'''
    (OUT / "doordash-llm-audit.html").write_text(html)
    print(json.dumps({k:v for k,v in coverage.items() if k not in ['queries','tree_coverage','limits']}, indent=2))


if __name__ == "__main__":
    build()
