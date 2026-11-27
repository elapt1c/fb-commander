"""Service abstraction.

Each Firebase product is one object with three capabilities:
  * ``probe``   - a cheap call that answers "can this key do this at all?"
  * ``tree``    - lazy navigation (RTDB, Firestore) or ``table`` (flat lists)
  * ``snapshot``- write everything this service owns into a backup folder

Denied services still render: the UI dims them and explains why, and a
"backup everything" run records the denial in the manifest instead of failing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..api import Api
from ..config import Endpoints
from ..models import Item, Preview, Probe, SnapshotResult, TablePage

Progress = Callable[[str, int | None, int | None], None]


def noop(message: str, done: int | None = None, total: int | None = None) -> None:
    return None


@dataclass
class Ctx:
    project_id: str
    api: Api
    endpoints: Endpoints


class Unsupported(Exception):
    """The service does not implement this navigation mode."""


class Service:
    key: str = ""
    label: str = ""
    icon: str = ""
    accent: str = "#ffa000"
    blurb: str = ""
    tree: bool = False

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.api = ctx.api
        self.endpoints = ctx.endpoints
        self.project = ctx.project_id

    # -- discovery -----------------------------------------------------
    def probe(self) -> Probe:
        raise NotImplementedError

    def tree_roots(self) -> list[Item]:
        raise Unsupported(self.key)

    def tree_children(self, item: Item) -> list[Item]:
        raise Unsupported(self.key)

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        raise Unsupported(self.key)

    def preview(self, item: Item) -> Preview:
        raise Unsupported(self.key)

    def download(self, item: Item, dest: Path, progress: Progress = noop) -> Path:
        raise Unsupported(self.key)

    # -- backup --------------------------------------------------------
    def snapshot(self, dest: Path, plan, progress: Progress = noop) -> SnapshotResult:
        raise Unsupported(self.key)


def write_json(path: Path, data, indent: int | None = 2) -> int:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=indent, ensure_ascii=False, default=str)
        if indent:
            handle.write("\n")
    return path.stat().st_size


def safe_join(root: Path, *parts: str) -> Path:
    """Join untrusted names under ``root`` without allowing traversal."""
    target = root
    for part in parts:
        for chunk in str(part).replace("\\", "/").split("/"):
            if chunk in ("", "."):
                continue
            chunk = chunk.replace("\x00", "_")
            if chunk == "..":
                chunk = "__"
            target = target / chunk
    target = target.resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"refusing to write outside {root_resolved}: {target}")
    return target


def human_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
