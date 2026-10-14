"""Illustrative report fixture. These are designed examples, never measured results."""

from gauge.models import Case, Comparison, Grade, ModelOutput, Outcome, Report


def demo_report() -> Report:
    inputs = [
        ("Organic whole milk · 1 gallon", "grocery"),
        ("Ultra soft toilet paper · 12 rolls", "household"),
        ("Wireless Bluetooth earbuds", "other"),
        ("Dishwasher detergent pods · lemon scent", "household"),
        ("Reusable grocery shopping bags", "household"),
        ("Dog food · chicken recipe · 15 lb", "grocery"),
        ("Vitamin C gummies · 60 count", "other"),
        ("Ground coffee · dark roast", "grocery"),
    ]
    names = ["reference", "qwen", "gpt-oss"]
    targets = {
        "reference": {"model": "gpt-5.6-terra", "api": "responses", "adapter": "illustrative-demo"},
        "qwen": {
            "model": "accounts/fireworks/models/qwen3p8-max",
            "api": "chat.completions",
            "adapter": "illustrative-demo",
        },
        "gpt-oss": {
            "model": "accounts/fireworks/models/gpt-oss-120b",
            "api": "chat.completions",
            "adapter": "illustrative-demo",
        },
    }
    cases, outcomes, comparisons = [], [], []
    for index, (text, label) in enumerate(inputs):
        case_id = f"demo_{index}"
        cases.append(Case(case_id, text, {"kind": "illustrative-demo"}))
        for column, name in enumerate(names):
            answer = "grocery" if (index, name) == (4, "qwen") else label
            if (index, name) == (6, "gpt-oss"):
                answer = "grocery"
            incomplete = (index, name) == (5, "qwen")
            thought = 4096 if incomplete else column * 47 + index * 3
            answer_tokens = 0 if incomplete else 2
            output = ModelOutput(
                "" if incomplete else answer,
                status="incomplete" if incomplete else "completed",
                usage={
                    "output_tokens": thought + answer_tokens,
                    "output_tokens_details": {
                        "reasoning_tokens": thought,
                        "text_tokens": answer_tokens,
                    },
                },
            )
            outcomes.append(
                Outcome(
                    case_id, name, 48000 if incomplete else 780 + column * 220 + index * 19, output
                )
            )
            if column:
                comparisons.append(
                    Comparison(
                        case_id,
                        name,
                        Grade(
                            "exact_match",
                            "1",
                            "baseline_agreement",
                            None if incomplete else float(answer == label),
                            status="not_scored" if incomplete else "completed",
                        ),
                    )
                )
    return Report(
        id="illustrative-demo",
        created_at="2026-10-12",
        instructions="Return exactly one label: grocery, household, or other.",
        reference="reference",
        targets=targets,
        cases=cases,
        outcomes=outcomes,
        comparisons=comparisons,
        metadata={"demo": True},
    )
