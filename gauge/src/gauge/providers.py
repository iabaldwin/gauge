"""Text-only evaluation adapters using existing, caller-owned async SDK clients."""

from __future__ import annotations

import copy
from typing import Any, Literal
from urllib.parse import urlsplit

from gauge.models import Case, ModelOutput

_COMMON = {"temperature", "top_p"}
_PARAMETERS = {
    "responses": _COMMON | {"max_output_tokens", "reasoning", "text", "truncation"},
    "chat.completions": _COMMON
    | {
        "max_tokens",
        "max_completion_tokens",
        "response_format",
        "seed",
        "stop",
        "reasoning_effort",
        "frequency_penalty",
        "presence_penalty",
    },
}


class OpenAITarget:
    """OpenAI Responses or compatible Chat Completions, including Fireworks.

    This maps one explicitly text-only case to each API. It is not a converter
    for arbitrary captured conversations, tools, images, or provider-held state.
    """

    def __init__(
        self,
        *,
        name: str,
        client: Any,
        model: str,
        api: Literal["responses", "chat.completions"] = "responses",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        if not isinstance(client, AsyncOpenAI):
            raise TypeError("OpenAITarget requires an AsyncOpenAI client.")
        if api not in _PARAMETERS:
            raise ValueError(f"Unsupported API: {api}")
        if not name or not model:
            raise ValueError("Target name and model must be nonempty.")
        params = copy.deepcopy(parameters or {})
        unknown = params.keys() - _PARAMETERS[api]
        if unknown:
            raise ValueError(f"Unsupported evaluation parameters: {sorted(unknown)}")
        if api == "responses":
            params.setdefault("max_output_tokens", 4096)
        elif "max_tokens" not in params and "max_completion_tokens" not in params:
            params["max_tokens"] = 4096
        if "max_tokens" in params and "max_completion_tokens" in params:
            raise ValueError("Choose one Chat Completions output token limit.")
        for key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
            if key in params and (type(params[key]) is not int or params[key] <= 0):
                raise ValueError(f"{key} must be a positive integer.")
        self.name, self.model, self.api = name, model, api
        self.client, self.parameters = client, params

    def describe(self) -> dict[str, Any]:
        endpoint = urlsplit(str(self.client.base_url))
        return {
            "adapter": "openai-sdk",
            "api": self.api,
            "model": self.model,
            "endpoint_host": endpoint.hostname,
            "endpoint_path": endpoint.path,
            "parameters": copy.deepcopy(self.parameters),
        }

    async def invoke(self, case: Case, *, instructions: str) -> ModelOutput:
        # Evaluation/generator calls must not feed themselves back into collection.
        from gauge.capture import suppress_capture

        request = {"model": self.model, **copy.deepcopy(self.parameters)}
        status: Literal["completed", "incomplete", "refused", "unsupported"]
        with suppress_capture():
            if self.api == "responses":
                request.update(instructions=instructions, input=case.input)
                response = await self.client.responses.create(**request)
                data = response.model_dump(mode="json")
                text = response.output_text
                status = "completed" if data.get("status") == "completed" else "incomplete"
                for item in data.get("output", []):
                    if item.get("type") not in {"message", "reasoning"}:
                        status = "unsupported"
                    for part in item.get("content", []):
                        if part.get("type") == "refusal":
                            status = "refused"
            else:
                request["messages"] = [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": case.input},
                ]
                response = await self.client.chat.completions.create(**request)
                data = response.model_dump(mode="json")
                text, status = None, "unsupported"
                if len(response.choices) == 1:
                    choice = response.choices[0]
                    text = choice.message.content
                    status = "completed" if choice.finish_reason == "stop" else "incomplete"
                    if choice.message.tool_calls:
                        status = "unsupported"
                    if choice.message.refusal:
                        status = "refused"
        if text is None and status == "completed":
            status = "unsupported"
        return ModelOutput(
            text=text,
            status=status,
            request=request,
            response=data,
            usage=data.get("usage") or {},
        )
