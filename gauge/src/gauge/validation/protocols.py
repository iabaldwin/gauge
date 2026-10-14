"""Native client calls only. Scenario identity and expected edges stay in the harness ledger."""

from __future__ import annotations

from gauge.backend.replay import response_text
from gauge.validation.records import Scenario


async def exchange(
    client,
    scenario: Scenario,
    turn: int,
    history: list[dict],
    previous: str | None,
    conversation: str | None,
    model: str,
    max_tokens: int = 256,
):
    message = {"role": "user", "content": scenario.turns[turn]["user"]}
    if scenario.protocol == "chat":
        return await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": scenario.instructions}, *history, message],
            max_completion_tokens=max_tokens,
        )
    options: dict = {}
    if scenario.protocol == "chain" and previous:
        options["previous_response_id"] = previous
    if scenario.protocol == "conversation":
        options["conversation"] = conversation
    return await client.responses.create(
        model=model,
        instructions=scenario.instructions,
        input=[*history, message] if scenario.protocol == "responses" else [message],
        max_output_tokens=max_tokens,
        **options,
    )


def outcome(response) -> tuple[dict, str | None, bool]:
    data = response.model_dump(mode="json")
    complete = (
        data.get("status") == "completed"
        if "output" in data
        else data["choices"][0].get("finish_reason") == "stop"
    )
    return data, response_text(data), complete
