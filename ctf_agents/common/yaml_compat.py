"""Small YAML loader wrapper with optional dependencies."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def safe_load_text(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        yaml = None
    if yaml is not None:
        return yaml.safe_load(text)

    try:
        from ruamel.yaml import YAML  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "YAML config loading requires PyYAML or ruamel.yaml"
        ) from exc
    return YAML(typ="safe").load(text)


def safe_load_file(path: str | Path) -> Any:
    return safe_load_text(Path(path).read_text(encoding="utf-8"))
