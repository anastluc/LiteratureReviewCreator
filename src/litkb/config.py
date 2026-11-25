"""Load config/default.toml, merged with an optional per-project config.toml and .env."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(project_dir: Path | None = None) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    cfg = tomllib.loads((REPO_ROOT / "config" / "default.toml").read_text(encoding="utf-8"))
    if project_dir and (project_dir / "config.toml").exists():
        cfg = _deep_merge(cfg, tomllib.loads((project_dir / "config.toml").read_text(encoding="utf-8")))
    return cfg


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)
