"""Pinned Multi-IF strict checks. No inference, downloads, or source-answer oracle."""

from __future__ import annotations

import contextlib
import io
import random
from functools import lru_cache

EXCLUDED = {"length_constraints:number_sentences", "change_case:capital_word_frequency"}


@lru_cache(maxsize=1)
def registry():
    import langdetect  # type: ignore[import-untyped]

    from gauge._vendor.multiif import INSTRUCTION_DICT

    langdetect.DetectorFactory.seed = 0
    return INSTRUCTION_DICT


def validate(spec: dict) -> None:
    ids, kwargs = spec["ids"], spec["kwargs"]
    if not ids or len(ids) != len(kwargs) or len(ids) > 12:
        raise ValueError("Malformed instruction constraints")
    for name, args in zip(ids, kwargs, strict=True):
        if name in EXCLUDED or name not in registry():
            raise ValueError("Instruction checker outside supported subset")
        if not isinstance(args, dict):
            raise ValueError("Instruction arguments must be an object")
        if name == "length_constraints:number_words" and args.get("num_words", 0) > 250:
            raise ValueError("Word constraint exceeds bounded subset")
        before = random.getstate()
        try:
            checker = registry()[name](name)
            checker.build_description(**args)
            if random.getstate() != before:
                raise ValueError("Constraint arguments would be randomly generated")
        finally:
            random.setstate(before)


def grade(spec: dict, text: str) -> dict:
    validate(spec)
    checks = []
    for name, args in zip(spec["ids"], spec["kwargs"], strict=True):
        checker = registry()[name](name)
        # Upstream diagnostics can include the response. Keep them out of server logs.
        with contextlib.redirect_stdout(io.StringIO()):
            checker.build_description(**args)
            passed = bool(text and checker.check_following(text))
        checks.append({"instruction": name, "arguments": args, "passed": passed})
    return {
        "status": "scored",
        "prompt_strict": float(all(c["passed"] for c in checks)),
        "instruction_strict": sum(c["passed"] for c in checks) / len(checks),
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "criterion": "All supplied instruction checks pass (strict)",
        "scorer": "Multi-IF 1cdb53ed strict; langdetect seed 0",
    }
