import json

import pytest

from gauge import Case, ModelOutput, Outcome, Report, normalize_usage


@pytest.mark.parametrize("api", ["responses", "chat"])
def test_reported_answer_and_reasoning_counts_are_distinct(api):
    usage = (
        {
            "input_tokens": 10,
            "output_tokens": 108,
            "output_tokens_details": {"reasoning_tokens": 100, "text_tokens": 3},
        }
        if api == "responses"
        else {
            "prompt_tokens": 10,
            "completion_tokens": 108,
            "completion_tokens_details": {"reasoning_tokens": 100, "text_tokens": 3},
        }
    )
    tokens = normalize_usage(usage, text_only=True, answer_text="household")
    assert tokens.input_tokens == 10
    assert tokens.total_output_tokens == 108
    assert tokens.reasoning_tokens == 100
    assert tokens.final_answer_tokens == 3
    assert tokens.non_reasoning_output_tokens == 8
    assert tokens.unattributed_output_tokens == 5
    assert "text_tokens" in tokens.sources["final_answer_tokens"]


def test_reasoning_only_length_limited_fireworks_response():
    # Counter shape observed in the user's incomplete dog-food classification.
    output = ModelOutput(
        text="",
        status="incomplete",
        usage={
            "completion_tokens": 4096,
            "prompt_tokens": 73,
            "total_tokens": 4169,
            "completion_tokens_details": {"reasoning_tokens": 4096, "text_tokens": None},
            "output_tokens_details": {"reasoning_tokens": 4096},
        },
    )
    tokens = output.token_usage
    assert tokens.reasoning_tokens == 4096  # Mirrored fields are not added twice.
    assert tokens.final_answer_tokens == 0
    assert tokens.total_output_tokens == 4096
    assert tokens.non_reasoning_output_tokens == 0
    assert tokens.sources["final_answer_tokens"] == "empty_answer_channel"


def test_total_minus_reasoning_is_not_exact_answer_count():
    output = ModelOutput(
        "grocery",
        usage={
            "output_tokens": 6,
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    )
    assert output.token_usage.reasoning_tokens == 0
    assert output.token_usage.final_answer_tokens is None
    assert output.token_usage.non_reasoning_output_tokens == 6
    assert output.token_usage.unattributed_output_tokens is None


def test_missing_reasoning_is_unknown_not_zero():
    tokens = normalize_usage({"completion_tokens": 12}, text_only=True, answer_text="other")
    assert tokens.total_output_tokens == 12
    assert tokens.reasoning_tokens is None
    assert tokens.final_answer_tokens is None
    assert tokens.non_reasoning_output_tokens is None


def test_generic_or_unsupported_output_does_not_claim_final_answer_tokens():
    usage = {"completion_tokens_details": {"text_tokens": 4}}
    assert normalize_usage(usage).final_answer_tokens is None
    assert (
        ModelOutput("tool call", status="unsupported", usage=usage).token_usage.final_answer_tokens
        is None
    )


@pytest.mark.parametrize("value", [-1, True, 1.5, "4"])
def test_invalid_counters_do_not_become_token_counts(value):
    tokens = normalize_usage({"output_tokens": value})
    assert tokens.total_output_tokens is None
    assert tokens.issues


def test_conflicting_aliases_are_unknown_and_flagged():
    tokens = normalize_usage(
        {
            "completion_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 25},
        }
    )
    assert tokens.total_output_tokens == 30
    assert tokens.reasoning_tokens is None
    assert tokens.non_reasoning_output_tokens is None
    assert tokens.issues == ("Conflicting counters: reasoning_tokens",)


def test_inconsistent_counters_never_produce_negative_remainder():
    tokens = normalize_usage(
        {
            "output_tokens": 10,
            "output_tokens_details": {"reasoning_tokens": 15, "text_tokens": 3},
        },
        text_only=True,
        answer_text="hello",
    )
    assert tokens.non_reasoning_output_tokens is None
    assert tokens.unattributed_output_tokens is None
    assert tokens.issues


def make_report(outcomes):
    return Report(
        id="run_test",
        created_at="2026-10-12",
        instructions="Task",
        reference="reference",
        targets={"reference": {"model": "reference"}},
        cases=[Case(str(index), "input") for index in range(len(outcomes))],
        outcomes=outcomes,
        comparisons=[],
    )


def test_report_includes_partial_call_usage_and_known_coverage():
    complete = ModelOutput(
        "yes",
        usage={
            "completion_tokens": 12,
            "completion_tokens_details": {"reasoning_tokens": 10, "text_tokens": 2},
        },
    )
    incomplete = ModelOutput(
        "",
        status="incomplete",
        usage={
            "completion_tokens": 4096,
            "completion_tokens_details": {"reasoning_tokens": 4096},
        },
    )
    report = make_report(
        [
            Outcome("0", "reference", 1, complete),
            Outcome("1", "reference", 2, incomplete),
        ]
    )
    totals = report.token_summary()["reference"]
    assert totals["total_output_tokens"] == 4108
    assert totals["reasoning_tokens"] == 4106
    assert totals["final_answer_tokens"] == 2
    assert totals["known_calls"]["final_answer_tokens"] == 2
    assert totals["input_tokens"] is None
    data = json.loads(report.to_json())
    assert data["outcomes"][0]["output"]["usage"] == complete.usage
    assert data["outcomes"][0]["output"]["token_usage"]["final_answer_tokens"] == 2
    assert data["token_summary"]["reference"]["reasoning_tokens"] == 4106


def test_errors_and_missing_usage_do_not_silently_reduce_report_totals():
    report = make_report(
        [
            Outcome("0", "reference", 1, ModelOutput("yes", usage={"output_tokens": 4})),
            Outcome("1", "reference", 1, error="TimeoutError"),
        ]
    )
    totals = report.token_summary()["reference"]
    assert totals["total_output_tokens"] is None
    assert totals["known_totals"]["total_output_tokens"] == 4
    assert totals["known_calls"]["total_output_tokens"] == 1
    assert totals["calls"] == 2
    assert totals["known_totals"]["reasoning_tokens"] is None


def test_zero_is_preserved_and_empty_reports_are_not_zero_usage():
    output = ModelOutput(
        "",
        usage={
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    )
    totals = make_report([Outcome("0", "reference", 1, output)]).token_summary()["reference"]
    assert totals["final_answer_tokens"] == totals["reasoning_tokens"] == 0
    assert make_report([]).token_summary()["reference"]["total_output_tokens"] is None
