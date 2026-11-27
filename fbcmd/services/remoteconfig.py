"""Remote Config: parameter inventory and JSON export."""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import ApiError
from ..models import Item, Preview, Probe, ServiceState, SnapshotResult, TablePage
from .base import Ctx, Service, noop, write_json


def _render_default(param: dict) -> str:
    value = param.get("defaultValue") or {}
    if "value" in value:
        return str(value["value"])
    if "useInAppDefault" in value:
        return "(in-app default)"
    return json.dumps(value, ensure_ascii=False)


class RemoteConfigService(Service):
    key = "remoteconfig"
    label = "Remote Config"
    icon = "⚙"
    accent = "#d2a8ff"
    blurb = "Flags & conditions"

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.url = f"{self.endpoints.remoteconfig}/v1/projects/{self.project}/remoteConfig"
        self._cache: dict | None = None

    def fetch(self, fresh: bool = False) -> dict:
        if self._cache is None or fresh:
            self._cache = self.api.get_json(self.url)
        return self._cache

    def probe(self) -> Probe:
        try:
            config = self.fetch(fresh=True)
        except ApiError as exc:
            if exc.kind == "not_found":
                return Probe(ServiceState.EMPTY, "Remote Config not initialised")
            raise
        params = config.get("parameters", {}) or {}
        return Probe(ServiceState.OK, f"{len(params)} parameter(s) · v{config.get('version', {}).get('versionNumber', '?')}")

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        config = self.fetch()
        params = config.get("parameters", {}) or {}
        rows = []
        items = []
        for key in sorted(params):
            param = params[key] or {}
            conditional = param.get("conditionalValues") or {}
            rows.append(
                [
                    key,
                    str(param.get("valueType", "STRING")),
                    _render_default(param)[:80],
                    str(len(conditional)) if conditional else "—",
                    str(param.get("description", "") or "—")[:60],
                ]
            )
            items.append(Item(key=key, label=key, kind="param", leaf=True, detail=str(param.get("valueType", "")), meta=param))
        conditions = config.get("conditions", []) or []
        return TablePage(
            title="Parameters",
            columns=["PARAMETER", "TYPE", "DEFAULT", "CONDITIONS", "DESCRIPTION"],
            rows=rows,
            items=items,
            breadcrumbs=[("Remote Config", "")],
            subtitle=f"{len(rows)} parameters · {len(conditions)} conditions · etag {str(config.get('etag', '') or '—')[:12]}",
            note="Read-only view: values are exported, nothing is published back.",
        )

    def preview(self, item: Item) -> Preview:
        config = self.fetch()
        if item.kind == "param":
            param = (config.get("parameters", {}) or {}).get(item.key, {})
            return Preview(title=f"parameter · {item.key}", body=json.dumps(param, indent=2, ensure_ascii=False))
        return Preview(title="Remote Config", body=json.dumps(config, indent=2, ensure_ascii=False))

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        config = self.fetch()
        target = dest if dest.suffix.lower() == ".json" else dest.with_suffix(".json")
        if item.kind == "param":
            write_json(target, (config.get("parameters", {}) or {}).get(item.key, {}))
        else:
            write_json(target, config)
        return target

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        config = self.fetch(fresh=True)
        base = dest / "remoteconfig"
        size = write_json(base / "remote_config.json", config)
        params = config.get("parameters", {}) or {}
        for key, param in params.items():
            write_json(base / "parameters" / f"{key}.json", param)
        progress("Remote Config written", len(params), len(params))
        return SnapshotResult(
            self.key,
            "ok",
            files=len(params) + 1,
            bytes=size,
            counts={"parameters": len(params), "conditions": len(config.get("conditions", []) or [])},
        )
