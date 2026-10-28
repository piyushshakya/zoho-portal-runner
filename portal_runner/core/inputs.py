"""Load the inputs JSON. Each portal validates its own section."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from portal_runner.core.errors import InputError


def load_inputs(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"inputs file not found: {path}") from None
    except json.JSONDecodeError as e:
        raise InputError(f"{path} is not valid JSON: {e.msg} (line {e.lineno})") from None
    if not isinstance(data, dict) or not isinstance(data.get("portal"), str):
        raise InputError(f'{path} must be a JSON object with a "portal" name')
    return data


def require(data: dict[str, Any], key: str, where: str) -> Any:
    if data.get(key) in ("", None, [], {}):
        raise InputError(f"{where}.{key} is required")
    return data[key]
