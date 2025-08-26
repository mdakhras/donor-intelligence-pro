# pydantic v2 friendly, safe for CosmosDB writes
from __future__ import annotations
import json
from typing import Any

def ensure_jsonable(obj: Any) -> Any:
    # primitives
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # containers
    if isinstance(obj, dict):
        return {str(k): ensure_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [ensure_jsonable(v) for v in obj]

    # common adapters
    for m in ("to_dict", "dict", "model_dump"):
        if hasattr(obj, m):
            try:
                v = getattr(obj, m)()
                return ensure_jsonable(v)
            except Exception:
                pass
    for m in ("json", "to_json"):
        if hasattr(obj, m):
            try:
                v = getattr(obj, m)()
                # might be a JSON string
                return json.loads(v)
            except Exception:
                pass

    # dataclasses
    try:
        import dataclasses
        if dataclasses.is_dataclass(obj):
            return ensure_jsonable(dataclasses.asdict(obj))
    except Exception:
        pass

    # last resort
    return str(obj)
