"""Paths inside a project workspace (one per concept note) + the shared PDF cache."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, load_config


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def projects_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    return _resolve(os.environ.get("LITKB_PROJECTS_DIR") or cfg["paths"]["projects_dir"])


@dataclass(frozen=True)
class Project:
    slug: str
    root: Path

    @classmethod
    def open(cls, slug: str) -> Project:
        root = projects_dir() / slug
        if not root.exists():
            raise FileNotFoundError(f"No project '{slug}' at {root}. Create it with `lit new {slug}`.")
        return cls(slug, root)

    @cached_property
    def config(self) -> dict[str, Any]:
        return load_config(self.root)

    def connect(self):
        from .db import connect
        return connect(self.db_path)

    @property
    def concept(self) -> Path: return self.root / "concept.md"
    @property
    def rqs_draft(self) -> Path: return self.root / "rqs.draft.yaml"
    @property
    def rqs(self) -> Path: return self.root / "rqs.yaml"
    @property
    def undermind_dir(self) -> Path: return self.root / "undermind"
    @property
    def papers_review(self) -> Path: return self.root / "papers_review.csv"
    @property
    def db_path(self) -> Path: return self.root / "papers.sqlite"
    @property
    def inbox_dir(self) -> Path: return self.root / "inbox"
    @property
    def parsed_dir(self) -> Path: return self.root / "parsed"
    @property
    def index_dir(self) -> Path: return self.root / "index.lance"
    @property
    def map_dir(self) -> Path: return self.root / "map"
    @property
    def reports_dir(self) -> Path: return self.root / "reports"

    @property
    def pdf_cache_dir(self) -> Path:
        return _resolve(os.environ.get("LITKB_PDF_CACHE_DIR") or self.config["paths"]["pdf_cache_dir"])

    @property
    def browser_profile_dir(self) -> Path:
        return _resolve(self.config["paths"]["browser_profile_dir"])

    def ensure_dirs(self) -> None:
        for d in (self.undermind_dir, self.inbox_dir, self.parsed_dir, self.map_dir, self.reports_dir,
                  self.pdf_cache_dir):
            d.mkdir(parents=True, exist_ok=True)


def list_projects() -> list[str]:
    d = projects_dir()
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "concept.md").exists()) if d.exists() else []


CONCEPT_TEMPLATE = """# Concept note

## Idea / problem
<What are you trying to understand? Why does it matter?>

## Context
<Ongoing project, experiment or study this supports. Methods, population, domain.>

## Scope
- In scope:
- Out of scope:
- Time range / disciplines / languages:

## What I already know (optional)
<Key papers, authors, terms.>
"""


def create_project(slug: str) -> Project:
    root = projects_dir() / slug
    if root.exists():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    p = Project(slug, root)
    p.ensure_dirs()
    p.concept.write_text(CONCEPT_TEMPLATE, encoding="utf-8")
    return p
