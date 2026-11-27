"""Realtime Database: lazy JSON tree navigation + full/subtree export."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from ..errors import ApiError
from ..models import Item, Preview, Probe, ServiceState, SnapshotResult
from .base import Ctx, Service, noop, write_json

PREVIEW_LIMIT = 2_000_000  # bytes of pretty-printed JSON to show in the pane


class RtdbService(Service):
    key = "rtdb"
    label = "Realtime Database"
    icon = "◈"
    accent = "#4dd0a7"
    blurb = "JSON tree, live values"
    tree = True

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self._base: str | None = None
        self.databases: list[str] = []

    # -- endpoint discovery -------------------------------------------
    def base_url(self) -> str:
        if self._base:
            return self._base
        try:
            data = self.api.get_json(
                f"{self.endpoints.rtdb_admin}/v1beta/projects/{self.project}/locations/-/instances"
            )
            urls = [str(i["databaseUrl"]).rstrip("/") for i in data.get("instances", []) if i.get("databaseUrl")]
            if urls:
                self.databases = urls
        except (ApiError, KeyError, TypeError):
            urls = []
        if not urls:
            urls = [f"https://{self.project}-default-rtdb.firebaseio.com"]
        # Prefer the default instance, then any us-central1 instance.
        urls.sort(key=lambda u: (0 if "default-rtdb" in u else 1, len(u)))
        self._base = urls[0]
        self.databases = sorted(set(urls))
        return self._base

    def _url(self, path: str) -> str:
        path = "/" + (path or "/").strip("/")
        if path == "/":
            return f"{self.base_url()}/.json"
        return f"{self.base_url()}{quote(path, safe='/~')}.json"

    def _get(self, path: str, **params):
        return self.api.get_json(self._url(path), params=params or None)

    # -- service -------------------------------------------------------
    def probe(self) -> Probe:
        base = self.base_url()
        data = self.api.get_json(self._url("/"), params={"shallow": "true", "limitToFirst": 1})
        count = len(data) if isinstance(data, dict) else 0
        return Probe(ServiceState.OK, f"{base.split('//')[-1]} · {count} root node(s)", count=count)

    def tree_roots(self) -> list[Item]:
        data = self._get("/", shallow="true")
        if not isinstance(data, dict):
            return []
        return [
            Item(key=f"/{key}", label=str(key), kind="node", leaf=False, detail="node")
            for key in sorted(data, key=str.lower)
        ]

    def tree_children(self, item: Item) -> list[Item]:
        try:
            data = self._get(item.key, shallow="true")
        except ApiError as exc:
            if exc.kind == "not_found":
                return []
            raise
        if not isinstance(data, dict):
            return []
        return [
            Item(key=f"{item.key.rstrip('/')}/{key}", label=str(key), kind="node", leaf=False, detail="node")
            for key in sorted(data, key=str.lower)
        ]

    def preview(self, item: Item) -> Preview:
        data = self._get(item.key)
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        truncated = len(body) > PREVIEW_LIMIT
        if truncated:
            body = body[:PREVIEW_LIMIT] + "\n\n… truncated for display; download for the full value."
        kind = type(data).__name__
        return Preview(
            title=item.key or "/",
            body=body,
            meta=[("type", kind), ("chars", f"{len(body):,}"), ("url", self._url(item.key))],
        )

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        progress(f"Fetching {item.key}", None, None)
        data = self._get(item.key)
        if dest.suffix.lower() not in (".json",):
            dest = dest.with_suffix(".json")
        write_json(dest, data, indent=2)
        return dest

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        path = getattr(plan, "rtdb_path", "/") or "/"
        progress(f"GET {path}", 0, 1)
        data = self._get(path)
        target = dest / "rtdb" / ("database.json" if path == "/" else "subtree.json")
        size = write_json(target, data, indent=2)
        nodes = self._count_nodes(data)
        note = {
            "database_url": self.base_url(),
            "path": path,
            "nodes": nodes,
        }
        write_json(dest / "rtdb" / "_meta.json", note)
        progress("Realtime Database written", 1, 1)
        return SnapshotResult(self.key, "ok", files=2, bytes=size, counts={"nodes": nodes, "path": path})

    @staticmethod
    def _count_nodes(data) -> int:
        total = 0
        stack = [data]
        while stack:
            node = stack.pop()
            total += 1
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return total
