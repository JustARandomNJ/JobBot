"""Load and lightly validate the application's YAML configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    """Return a YAML mapping, raising a useful error for malformed files."""
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return value


def load_configuration(config_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load candidate, preferences, and company configuration files."""
    return (
        load_yaml(config_dir / "candidate_profile.yaml"),
        load_yaml(config_dir / "preferences.yaml"),
        load_yaml(config_dir / "companies.yaml"),
    )

