"""Build the standalone research presentation: python3 -m research.discovery.present."""
from collections import Counter
from html import escape
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[2]


def read_json(path):
    return json.loads((ROOT / path).read_text())


def e(value):
    return escape(str(value), quote=True)


def build():
    content = read_json('data/presentation.json')
    assessment = read_json('data/doordash-conclusions.json')['directions']
    taxonomy = read_json('data/verticals.json')['verticals']
    verticals = {v['id']: v for v in taxonomy}
    queries = {q['id']: q for q in read_json('data/research/queries.json')}
    registry = {s['id']: s for s in read_json('data/research/sources.json')}
    titles = content['source_titles']
    cited = set()

    def source_links(ids):
        links = []
        for source_id in ids:
            query = queries[source_id]
            assert query['status'] == 'saved', source_id
            assert (ROOT / 'data/research' / query['file']).exists(), source_id
            cited.add(source_id)
            source = registry.get(source_id)
            url = source['url'] if source else '../data/research/' + query['file']
            title = titles.get(source_id, 'Search: ' + query['args'].get('query', source_id))
            links.append(f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(title)} ↗</a>')
        return '<div class="source-links">' + ''.join(links) + '</div>'

    def companies(v):
        return '<ul class="company-list">' + ''.join(
            f'<li><span class="mono">{e(c["batch"])}</span><a href="https://www.ycombinator.com/companies/{e(c["slug"])}" target="_blank" rel="noopener noreferrer">{e(c["name"])}</a></li>'
            for c in v['companies']) + '</ul>'

    def related(theme):
        items = []
        for vid in theme['vertical_ids']:
            v = verticals[vid]
            items.append(f'<div><h4>{e(v["label"].capitalize())}</h4><p>{v["count"]} companies · W/X/S/F: ' + ' / '.join(str(v['counts'][b]) for b in ['W26','X26','S26','F26']) + f'</p>{companies(v)}<p><a href="#direction-{e(vid)}">Read the exact-fit assessment →</a></p></div>')
        return '<details class="related"><summary>Related YC directions &amp; companies</summary><div class="related-content">' + ''.join(items) + '</div></details>'

    def evidence_block(theme):
        evidence = ''.join(f'<article class="evidence"><div class="eyebrow">{e(item["date"])}</div><h3>{e(item["title"])}</h3><p>{e(item["text"])}</p>{source_links(item["sources"])}</article>' for item in theme['evidence'])
        metrics = ''.join(f'<span>{e(m)}</span>' for m in theme['measure'])
        return f'''<div class="theme-body"><div><div class="mini-heading">What the sources establish</div><div class="evidence-list">{evidence}</div></div><aside class="hypothesis"><div class="eyebrow">Our hypothesis / not validated demand</div><h3>{e(theme['hypothesis'])}</h3><h4>Compare with</h4><p>{e(theme['baseline'])}</p><h4>Measure the difference</h4><div class="metrics">{metrics}</div><div class="test"><div class="eyebrow">First conversation</div><p>{e(theme['interview'])}</p></div></aside></div><div class="theme-bottom"><div class="alternative"><h4>What already exists</h4><p>{e(theme['alternative'])}</p>{source_links(theme['alternative_sources'])}</div><p class="boundary"><strong>Keep the claim bounded.</strong> {e(theme['caveat'])}</p></div>'''

    batches = {b: sum(v['counts'][b] for v in taxonomy) for b in ['W26','X26','S26','F26']}
    total = sum(batches.values())
    saved = sum(q['status'] == 'saved' for q in queries.values())
    singleton = sum(v['count'] == 1 for v in taxonomy)
    fit_key = lambda d: 'direct' if d['fit'].startswith('Direct') else 'adjacent' if d['fit'] == 'Adjacent' else 'unestablished'
    fit_counts = Counter(fit_key(d) for d in assessment)
    themes = content['themes']
    out = []
    out.append(f'''<section class="section hero" id="overview" data-title="The thesis"><div class="wrap"><div class="hero-top"><span class="eyebrow">From funded ideas to operating problems</span><span class="mono muted">RESEARCH EDITION / {e(content['as_of']).upper()}</span></div><div class="hero-grid"><div><h1>Where the evidence<br>points next.</h1><p class="deck">Three strong investigations emerge where AI meets production: trustworthy evaluation, verified knowledge, and better model economics.</p><div class="hero-actions"><a class="button primary" href="#evaluations">Read the findings ↘</a><a class="textlink" href="#landscape">Start with the landscape</a></div></div><aside class="hero-stats" aria-label="Research scope"><div class="stat"><strong>{total}</strong><span>YC companies in the snapshot</span></div><div class="stat"><strong>{len(assessment)}</strong><span>directions investigated</span></div><div class="stat"><strong>{saved}</strong><span>saved research responses</span></div><div class="stat"><strong>{len(themes)}</strong><span>themes for further discovery</span></div></aside></div><div class="thesis"><span class="eyebrow">The central finding</span><p>An existing internal tool can demonstrate demand while making a new product harder to sell. The question is what work remains.</p></div><div class="chapter-index">''')
    for theme in themes[:3]:
        out.append(f'<a class="chapter-link" href="#{theme["id"]}"><div class="mono muted">{theme["number"]} / {e(theme["label"].upper())}<span class="arrow">↗</span></div><h3>{e(theme["headline"])}</h3><p>{e(theme["deck"])}</p></a>')
    out.append('</div><p class="note">Internal evidence validates problems, not external willingness to pay. Findings reflect sources retrieved on 11 October 2026.</p></div></section>')
    bar_rows = ''.join(f'<div class="bar-row"><span>{label}</span><div class="bar-track" aria-hidden="true"><div class="bar {"incomplete" if b == "F26" else ""}" style="width:{n/max(batches.values())*100:.2f}%"></div></div><span>{n}</span></div>' for (b,n),label in zip(batches.items(),['Winter 26','Spring 26','Summer 26','Fall 26*']))
    out.append(f'''<section class="section" id="landscape" data-title="The landscape"><div class="wrap"><div class="section-head"><div><div class="eyebrow">The landscape / how we got here</div><h2>Funded ideas are a starting point.</h2></div><span class="page-no mono">W26 → F26</span></div><div class="two-col"><div><p class="lead">We grouped companies by buyer, job, and workflow, then tested a shortlist against real operating problems inside DoorDash.</p><div class="landscape-bars" role="img" aria-label="YC company counts: Winter 198, Spring 193, Summer 230, Fall 126. Fall is incomplete.">{bar_rows}</div><p class="note">* Fall is incomplete and excluded from trend rules. Public YC directory snapshot: 10 October 2026, 16:32 UTC.</p></div><div><div class="mini-heading">634 verticals. Most contain one company.</div><div class="density" role="img" aria-label="559 of 634 verticals are singletons; 75 have multiple companies."><span style="flex:{singleton}"></span><span style="flex:{len(taxonomy)-singleton}"></span></div><div class="legend"><span><b>{singleton}</b> singletons</span><span><b>{len(taxonomy)-singleton}</b> multi-company groups</span></div><p class="note"><strong>Taxonomy density is not market white space.</strong> Label granularity changes the counts. Six groups meet the four-company dense cutoff; 26 are thin neighbors, of which ten entered the shortlist.</p><details class="related"><summary>Three corrections to the original interpretation</summary><div class="small muted" style="padding-bottom:18px"><p><strong>PerfectBit:</strong> its long description supports learned manipulation/control despite a vague one-liner.</p><p><strong>Manifold:</strong> loading/unloading and palletizing make the picking group less pure than its label implies.</p><p><strong>Buying signals:</strong> Cardinal and Gojiberry already describe intent/signals. Clean's singleton label does not establish an uncontested market.</p></div></details></div></div><div class="method-row"><div><h3>01 / Need</h3><p>Does a source explicitly name a cost, failure, repeated task, or goal?</p></div><div><h3>02 / Fit</h3><p>Does that need match the proposed buyer and job, or only an adjacent workflow?</p></div><div><h3>03 / Remaining gap</h3><p>What remains after the current tools, planned work, and follow-up fixes?</p></div></div></div></section>''')
    for theme in themes[:3]:
        out.append(f'''<section class="section" id="{theme['id']}" data-title="{e(theme['label'])}"><div class="wrap"><div class="theme-top"><span class="big-number">{theme['number']}</span><div><div class="eyebrow">Priority investigation / {e(theme['label'])}</div><h2>{e(theme['headline'])}</h2><p class="lead">{e(theme['deck'])}</p><div class="signal-line"><span class="signal">{e(theme['signal'])}</span><span class="baseline">Baseline: {e(theme['baseline'])}</span></div></div></div>{evidence_block(theme)}{related(theme)}</div></section>''')
    out.append('''<section class="section next-wave" id="next-wave" data-title="Three narrower investigations"><div class="wrap"><div class="section-head"><div><div class="eyebrow">The next wave / grounded, narrower problems</div><h2>Follow the work into the operation.</h2></div><span class="page-no mono">04 — 06</span></div><p class="lead">Merchant teams and robotics operations have concrete needs. Their exact workflows matter more than the broad startup category.</p>''')
    for theme in themes[3:]:
        out.append(f'''<details class="wave" id="{theme['id']}"><summary><span class="wave-number">{theme['number']}</span><span><h3>{e(theme['label'])}</h3><p>{e(theme['deck'])}</p></span><span class="plus" aria-hidden="true">+</span></summary><div class="wave-body"><div class="signal-line"><span class="signal">{e(theme['signal'])}</span></div>{evidence_block(theme)}<div class="note"><strong>First test:</strong> {e(theme['test'])}<br><strong>Reject if:</strong> {e(theme['reject'])}</div>{related(theme)}</div></details>''')
    out.append('</div></section>')
    out.append('''<section class="section" id="directions" data-title="All 20 directions"><div class="wrap"><div class="section-head"><div><div class="eyebrow">The complete shortlist / no silent omissions</div><h2>Every direction, with its limits.</h2></div><span class="page-no mono">20 ASSESSMENTS</span></div><p class="lead">Direct need, adjacent evidence, or not established in this search. These labels assess fit—not market size or purchase intent.</p><div class="filters" role="group" aria-label="Filter by evidence fit">''')
    for key,label,n in [('all','All',len(assessment)),('direct','Direct',fit_counts['direct']),('adjacent','Adjacent',fit_counts['adjacent']),('unestablished','Not established',fit_counts['unestablished'])]:
        out.append(f'<button type="button" class="filter" data-filter="{key}" aria-pressed="{str(key == "all").lower()}">{label}<span>{n}</span></button>')
    out.append('<div class="small muted" style="width:100%;margin-top:8px">Direct = stated need matches the job · Adjacent = buyer, channel, or workflow changes · Not established = this pass did not find support</div></div><div class="searchbox"><label for="direction-search">Find a direction</label><input id="direction-search" type="search" placeholder="Direction, company, or workflow…"><span class="countline" id="results-count" role="status" aria-live="polite">20 of 20 directions</span></div>')
    for i,d in enumerate(assessment,1):
        vid=d['vertical_id'];v=verticals[vid];key=fit_key(d)
        conclusion=re.sub(r'\s*\(#\d\)', '', d['conclusion'])
        search_text=' '.join([d['direction'],d['conclusion'],v['buyer'],v['job']]+[c['name'] for c in v['companies']]).lower()
        batch=' · '.join(f'{b} {v["counts"][b]}' for b in ['W26','X26','S26','F26'])
        company_items=''.join(f'<li><a href="https://www.ycombinator.com/companies/{e(c["slug"])}" target="_blank" rel="noopener noreferrer">{e(c["name"])}</a> <span class="mono">{e(c["batch"])}</span> — {e(c["one_liner"])}</li>' for c in v['companies'])
        fullfit='Direct' if key=='direct' else 'Adjacent' if key=='adjacent' else 'Not established'
        out.append(f'''<details class="direction" id="direction-{e(vid)}" data-fit="{key}" data-search="{e(search_text)}"><summary><span class="mono muted">{i:02}</span><span class="dir-title">{e(d['direction'])}</span><span class="fit {key}">{fullfit}</span><span class="dir-short">{e(conclusion)}</span><span class="dir-plus" aria-hidden="true">+</span></summary><div class="direction-body"><div><h4>{e(d['fit'])}</h4><p>{e(conclusion)}</p><p class="note">Buyer: {e(v['buyer'])} · Job: {e(v['job'])}<br>External purchase: not validated.</p><h4 style="margin-top:18px">Evidence and search trail</h4>{source_links(d['evidence_ids'])}</div><div><h4>{v['count']} YC companies in the snapshot</h4><div class="batch-counts">{batch}</div><ul>{company_items}</ul></div></div></details>''')
    out.append('<p class="no-results" id="no-results" hidden>No matching directions. Try another search or choose All.</p></div></section>')
    out.append('''<section class="section" id="next-tests" data-title="The next experiments"><div class="wrap"><div class="section-head"><div><div class="eyebrow">Research → action / proposed experiments</div><h2>Earn the next investment of time.</h2></div><span class="page-no mono">START WITH THREE</span></div><p class="lead">Take a recent concrete case, quantify the recurring work, and compare the hypothesis with the existing answer.</p><div class="next-step-list">''')
    for theme in themes[:3]:
        out.append(f'''<article class="next-step"><span class="num">{theme['number']}</span><div><h3>{e(theme['label'])}</h3><p>{e(theme['interview'])}</p><div class="metrics">{''.join(f'<span>{e(m)}</span>' for m in theme['measure'])}</div></div><div><div class="test-title">Proposed test</div><p>{e(theme['test'])}</p><details><summary>What would change our mind?</summary><p>{e(theme['reject'])}</p></details></div></article>''')
    out.append('''</div><div class="ending"><h3>A real problem is the beginning of a business case.</h3><p>Next, establish recurrence outside DoorDash, budget ownership, procurement constraints, and willingness to switch. The strongest internal evidence still needs a buyer.</p></div></div></section>''')
    counts=Counter(q['tool'] for q in queries.values() if q['status']=='saved')
    out.append(f'''<section class="section" id="method" data-title="Sources and method"><div class="wrap"><div class="section-head"><div><div class="eyebrow">Research notes / provenance and boundaries</div><h2>Keep the evidence inspectable.</h2></div><span class="page-no mono">AS OF 11 OCT 2026</span></div><div class="method-content"><div><h3>What was read</h3><p>{len(queries)} logged read requests; {saved} saved responses. The saved set contains {counts['slack_search_public']} Slack searches, {counts['slack_read_thread']} thread reads, {counts['search']} Atlassian searches, {counts['get-confluence-page']} page bodies, and {counts['get-jira-issue']} current Jira issues.</p><h3>What changed the conclusion</h3><p>The first pass treated an existing internal system as evidence against demand. This pass separates need, exact workflow fit, and the remaining gap. Thread follow-ups distinguish historical incidents from current unresolved work.</p><h3>How the YC shortlist was formed</h3><p>Company descriptions were labeled by buyer, job, and workflow, then near-duplicates merged within domains. Dense means at least four companies; consensus means presence in each completed batch; a surge needs at least two Summer companies and more than Winter plus Spring combined.</p></div><div><h3>Where the search stops</h3><ul><li>Searches were bounded and relevance-limited, not exhaustive.</li><li>One private routing-launch thread was unavailable. Its public forwarding discussion was read.</li><li>Linked Google Docs, dashboards, PR bodies, and media were not independently read.</li><li>Source dates reflect retrieved evidence; they do not guarantee unchanged current status.</li><li>Reported pilot benefits and planned work are not general production results.</li></ul><h3>Reading the source links</h3><p>Slack, Confluence, and Jira links require workspace access. Search-only evidence links open the saved local response. All company names link to their YC profiles; descriptions and counts remain the October 10 snapshot.</p><h3>Research files</h3><p><a href="doordash-discovery.md">Full findings</a> · <a href="brief.md">YC shortlist</a> · <a href="../data/doordash-conclusions.json">Structured assessments</a> · <a href="../data/research/queries.json">Query log</a> · <a href="archive/doordash-discovery-first-pass.md">First pass</a></p></div></div>''')
    indexed=[]
    for source_id in sorted(cited, key=lambda s: titles.get(s,s)):
        q=queries[source_id];source=registry.get(source_id)
        if not source:continue
        kind='Slack' if q['service']=='slack' else 'Jira' if q['tool']=='get-jira-issue' else 'Confluence'
        indexed.append(f'<li><a href="{e(source["url"])}" target="_blank" rel="noopener noreferrer">{e(titles[source_id])} ↗</a><span class="mono">{kind}</span></li>')
    out.append(f'<details class="source-index"><summary>Source index · {len(indexed)} cited pages, threads, and issues</summary><ul class="source-grid">'+''.join(indexed)+'</ul></details></div></section>')
    template=Path(__file__).with_name('presentation.html').read_text()
    assert template.count('<!-- CONTENT -->') == 1
    rendered=template.replace('<!-- CONTENT -->','\n'.join(out))
    destination=ROOT/'out/index.html'
    old=ROOT/'out/yc-landscape.html'
    if destination.exists() and not old.exists():
        old.write_text(destination.read_text())
    destination.write_text(rendered)
    print(f'Built {destination}: {len(themes)} themes, {len(assessment)} directions, {len(indexed)} linked sources, {len(rendered.encode()):,} bytes.')


if __name__=='__main__':
    build()
