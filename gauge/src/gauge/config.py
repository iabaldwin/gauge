"""Default backend comparison policy; never evaluated on the serving path."""

from collections.abc import Mapping

DEFAULT_FIREWORKS_MODELS = (
    "accounts/fireworks/models/qwen3p8-max",
    "accounts/fireworks/models/gpt-oss-120b",
)


def comparison_models(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Plural replaces the list; legacy singular adds a preferred model to defaults."""
    if "FIREWORKS_MODELS" in environ:
        models = [item.strip() for item in environ["FIREWORKS_MODELS"].split(",") if item.strip()]
    else:
        preferred = environ.get("FIREWORKS_MODEL", "").strip()
        models = [*([preferred] if preferred else []), *DEFAULT_FIREWORKS_MODELS]
    if not models:
        raise ValueError("FIREWORKS_MODELS must contain at least one model ID.")
    return tuple(dict.fromkeys(models))
