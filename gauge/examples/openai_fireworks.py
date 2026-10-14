"""Backend evaluation job: probe candidates, generate inputs, write JSON and HTML."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

from openai import APIError, AsyncOpenAI

from gauge import Case, Experiment, OpenAITarget, generate_cases
from gauge.config import comparison_models

INSTRUCTIONS = "Return exactly one label: grocery, household, or other."


async def check_candidate(candidate: OpenAITarget) -> None:
    """Catch inaccessible models before generating a dataset or running the reference."""
    try:
        async with asyncio.timeout(60):
            output = await candidate.invoke(
                Case("preflight", "Dish soap"), instructions=INSTRUCTIONS
            )
    except (APIError, TimeoutError) as exc:
        status = getattr(exc, "status_code", None)
        detail = f" (HTTP {status})" if status else ""
        hint = (
            " Check model access and serving availability: Fireworks catalog models "
            "may require a dedicated deployment."
            if status == 404
            else " Check provider credentials, quotas, and connectivity."
        )
        raise SystemExit(
            f"Candidate check failed for {candidate.model}: {type(exc).__name__}{detail}."
            f"{hint} No synthetic data or reference calls were dispatched."
        ) from None
    if output.status != "completed" or not output.text:
        raise SystemExit(
            f"Candidate check returned {output.status} without a usable complete answer. "
            "Check output limits and model settings before running the evaluation."
        )


async def main() -> None:
    async with (
        AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=60) as source,
        AsyncOpenAI(
            api_key=os.environ["FIREWORKS_API_KEY"],
            base_url="https://api.fireworks.ai/inference/v1",
            max_retries=0,
            timeout=60,
        ) as destination,
    ):
        reference = OpenAITarget(
            name="openai-reference",
            client=source,
            model=os.environ.get("GAUGE_REFERENCE_MODEL", "gpt-5.6-terra"),
            api="responses",
            parameters={"max_output_tokens": 4096},
        )
        candidates = []
        failures = {}
        for model in comparison_models(os.environ):
            candidate = OpenAITarget(
                name=f"fireworks/{model.rsplit('/', 1)[-1]}",
                client=destination,
                model=model,
                api="chat.completions",
                parameters={"max_tokens": 4096},
            )
            try:
                await check_candidate(candidate)
            except SystemExit as exc:
                failures[model] = str(exc)
                print(f"Skipping unavailable candidate: {exc}")
            else:
                candidates.append(candidate)
        if not candidates:
            raise SystemExit("No candidates are available. " + "\n".join(failures.values()))
        cases = await generate_cases(
            generator=reference,
            task="Classify retail products as grocery, household, or other.",
            count=20,
        )
        report = await Experiment(
            instructions=INSTRUCTIONS,
            reference=reference,
            candidates=candidates,
            max_concurrency=8,
            max_cases=20,
            max_calls=20 * (1 + len(candidates)),
        ).run(cases)
        report = replace(report, metadata={"preflight_failures": failures})
        report_path = Path(f"gauge-report-{report.id}.json")
        html_path = report_path.with_suffix(".html")
        await asyncio.to_thread(
            report_path.write_text,
            report.to_json() + "\n",
        )
        await asyncio.to_thread(html_path.write_text, report.to_html(), encoding="utf-8")
        print(
            json.dumps(
                {
                    "summary": report.summary(),
                    "targets": report.diagnostics(),
                    "tokens": report.token_summary(),
                },
                indent=2,
            )
        )
        print(f"Full report: {report_path}")
        print(f"HTML comparison: {html_path}")
        if not any(item.agreement.value is not None for item in report.comparisons):
            raise SystemExit("No comparisons were scored; inspect the target errors above.")


if __name__ == "__main__":
    asyncio.run(main())
