from __future__ import annotations

from pathlib import Path
from typing import Any
import os
from .yaml_compat import safe_load_file

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

load_dotenv()
DEFAULT_CONFIG_PATH = Path("configs/config.yaml")


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置不存在: {path}. 先 cp configs/config.example.yaml {path}")
    cfg = safe_load_file(path) or {}
    cfg.setdefault("env", {})
    for key in ["FEISHU_WEBHOOK", "FEISHU_SECRET", "CTF_PLATFORM_BASE_URL", "CTF_PLATFORM_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"]:
        cfg["env"][key] = os.getenv(key, "")
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for p in cfg.get("paths", {}).values():
        if isinstance(p, str) and not p.endswith(".json"):
            Path(p).mkdir(parents=True, exist_ok=True)
