"""Import-swap constructors; inference remains implemented by the native SDK."""

from typing import Any

from openai import AsyncOpenAI as NativeAsyncOpenAI
from openai import OpenAI as NativeOpenAI

from gauge.capture import _SUPPRESSED, wrap


class OpenAI(NativeOpenAI):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not _SUPPRESSED.get():
            try:
                wrap(self)
            except Exception:
                # Optional instrumentation must not undo successful native setup.
                pass


class AsyncOpenAI(NativeAsyncOpenAI):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not _SUPPRESSED.get():
            try:
                wrap(self)
            except Exception:
                # Optional instrumentation must not undo successful native setup.
                pass
