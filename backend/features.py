"""Feature toggles: one place that decides which parts of the app are switched on.

Everything else asks a `Features` value (injected), never `os.environ`. Precedence per flag:
explicit `FEATURE_<NAME>` env var > `APP_MODE` preset > default.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields

from dotenv import load_dotenv

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

# APP_MODE kept as a compatibility preset over the individual flags.
_PRESETS: dict[str, dict[str, bool]] = {
    "annotation": {
        "search": False,
        "benchmark": False,
        "dense_retrieval": False,
        "eager_model": False,
    },
    "search": {"eager_model": False},
    "research": {"eager_model": True},
}


@dataclass(frozen=True)
class Features:
    annotation: bool = True
    kv_pairs: bool = True
    benchmark: bool = True
    search: bool = True
    dense_retrieval: bool = True  # E5 embeddings + model (semantic endpoints)
    eager_model: bool = False  # load the E5 model at startup instead of on first request
    finetuned_adapter_path: str = ""  # LoRA adapter merged into the E5 model when set

    def is_enabled(self, name: str) -> bool:
        return bool(getattr(self, name))


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be one of {sorted(_TRUE | _FALSE)}, got {raw!r}")


def load_features(env: Mapping[str, str] | None = None) -> Features:
    """Build `Features` from the environment (or an explicit mapping, e.g. in tests)."""
    if env is None:
        load_dotenv()
        env = os.environ

    values: dict = {}
    values.update(_PRESETS.get(env.get("APP_MODE", "search"), {}))
    for field in fields(Features):
        if field.type is bool:
            raw = env.get(f"FEATURE_{field.name.upper()}")
            if raw is not None:
                values[field.name] = _parse_bool(f"FEATURE_{field.name.upper()}", raw)
    values["finetuned_adapter_path"] = env.get("FINETUNED_ADAPTER_PATH", "")
    return Features(**values)
