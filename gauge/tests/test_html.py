import json
from dataclasses import replace
from html.parser import HTMLParser

import pytest

from gauge.__main__ import main
from gauge.config import DEFAULT_FIREWORKS_MODELS, comparison_models
from gauge.demo import demo_report
from gauge.html import report_requests
from gauge.models import ModelOutput, Report


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_report_has_all_models_cases_and_issues_without_external_assets():
    html = demo_report().to_html()
    tags = Elements(html).tags
    records = report_requests(demo_report())
    assert len(records) == 8
    assert all(len(record["candidates"]) == 2 for record in records)
    assert sum(c["status"] == "skipped" for r in records for c in r["candidates"]) == 1
    assert records[5]["candidates"][0]["tokens"]["reasoning"] == 4096
    assert "Illustrative demo" in html
    assert all("src" not in attrs and "href" not in attrs for _, attrs in tags)


def test_report_escapes_payloads_and_does_not_expand_user_placeholders():
    report = demo_report()
    payload = '</td><script>alert("x")</script><img src=x onerror=alert(1)> {{ROWS}}'
    report = replace(
        report,
        cases=[replace(case, input=payload) for case in report.cases],
        outcomes=[replace(item, output=ModelOutput(payload)) for item in report.outcomes],
        metadata={"preflight_failures": {payload: payload}},
        instructions=payload,
    )
    html = report.to_html()
    tags = Elements(html).tags
    assert sum(tag == "script" for tag, _ in tags) == 3
    assert all(tag != "img" for tag, _ in tags)
    assert "\\u003cscript\\u003e" in html
    assert "{{ROWS}}" in html
    assert report_requests(report)[0]["original"]["tokens"]["answer"] is None


def test_report_round_trip_recomputes_derived_usage():
    report = demo_report()
    data = json.loads(report.to_json())
    assert Report.from_dict(data) == report
    data["token_summary"] = {"incorrect": 99999}
    for item in data["outcomes"]:
        item["output"]["token_usage"] = {"incorrect": 99999}
    loaded = Report.from_dict(data)
    assert loaded.token_summary() == report.token_summary()
    assert loaded == report
    with pytest.raises(ValueError, match="schema"):
        Report.from_dict({**data, "schema_version": "999"})


def test_cli_renders_saved_report_and_refuses_overwriting_input(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    original = demo_report().to_json()
    path.write_text(original)
    monkeypatch.setattr("sys.argv", ["gauge", "report", str(path)])
    main()
    assert "Model comparison" in path.with_suffix(".html").read_text()
    monkeypatch.setattr("sys.argv", ["gauge", "report", str(path), "--output", str(path)])
    with pytest.raises(SystemExit):
        main()
    assert path.read_text() == original


def test_default_models_and_environment_overrides():
    assert len(comparison_models({})) == 3
    assert comparison_models({}) == DEFAULT_FIREWORKS_MODELS
    assert comparison_models({"FIREWORKS_MODELS": " a, b, a, "}) == ("a", "b")
    with pytest.raises(ValueError, match="at least one"):
        comparison_models({"FIREWORKS_MODELS": " , "})
