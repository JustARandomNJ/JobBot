from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(name: str) -> dict[str, Any]:
    path = Path(__file__).parents[1] / "config" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))

