"""Firebase Security Rules: release -> ruleset -> source files."""

from __future__ import annotations

from pathlib import Path

from ..errors import ApiError
from ..models import Item, Preview, Probe, ServiceState, SnapshotResult, TablePage
from .base import Ctx, Service, noop, write_json


class RulesService(Service):
    key = "rules"
    label = "Security Rules"
    icon = "⚿"
    accent = "#f778ba"
    blurb = "Rulesets & releases"

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.base = f"{self.endpoints.rules}/v1/projects/{self.project}"

    def _releases(self) -> list[dict]:
        return self.api.get_json(f"{self.base}/releases").get("releases", []) or []

    def _ruleset(self, name: str) -> dict:
        return self.api.get_json(f"{self.endpoints.rules}/v1/{name}")

    def probe(self) -> Probe:
        releases = self._releases()
        return Probe(ServiceState.OK, f"{len(releases)} release(s)", count=len(releases))

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        releases = self._releases()
        rows = []
        items = []
        for rel in releases:
            short = str(rel.get("name", "")).rsplit("/", 1)[-1]
            ruleset = str(rel.get("rulesetName", "")).rsplit("/", 1)[-1]
            rows.append([short, ruleset, str(rel.get("updateTime", "—"))[:19].replace("T", " ")])
            items.append(Item(key=str(rel.get("rulesetName")), label=short, kind="release", detail=ruleset, meta=rel))
        return TablePage(
            title="Releases",
            columns=["RELEASE", "RULESET", "UPDATED"],
            rows=rows,
            items=items,
            breadcrumbs=[("Security Rules", "")],
            subtitle=f"{len(rows)} ruleset release(s) live",
            note="Enter a release to read its rules source.",
        )

    def preview(self, item: Item) -> Preview:
        ruleset = self._ruleset(item.key)
        files = (ruleset.get("source") or {}).get("files", []) or []
        body = "\n\n".join(f"// ---- {f.get('name', 'rules')} ----\n{f.get('content', '')}" for f in files) or "(no source)"
        return Preview(
            title=f"{item.label} · {item.key.rsplit('/', 1)[-1]}",
            body=body,
            language="text",
            meta=[("ruleset", item.key), ("files", str(len(files)))],
        )

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        if item.kind == "release":
            ruleset = self._ruleset(item.key)
            target = dest if dest.suffix == "" else dest.with_suffix("")
            target.mkdir(parents=True, exist_ok=True)
            write_json(target / "ruleset.json", ruleset)
            for entry in (ruleset.get("source") or {}).get("files", []) or []:
                name = str(entry.get("name", "rules")).replace("/", "_")
                (target / name).write_text(str(entry.get("content", "")), encoding="utf-8")
            return target
        target = dest if dest.suffix.lower() == ".json" else dest.with_suffix(".json")
        write_json(target, item.meta or {})
        return target

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        base = dest / "rules"
        releases = self._releases()
        write_json(base / "releases.json", releases)
        files = 1
        seen: set[str] = set()
        for rel in releases:
            name = str(rel.get("rulesetName", ""))
            if not name or name in seen:
                continue
            seen.add(name)
            short = name.rsplit("/", 1)[-1]
            progress(f"ruleset {short}", len(seen), len(releases))
            try:
                ruleset = self._ruleset(name)
            except ApiError as exc:
                write_json(base / "rulesets" / f"{short}.error.json", {"error": str(exc)})
                files += 1
                continue
            write_json(base / "rulesets" / f"{short}.json", ruleset)
            files += 1
            for entry in (ruleset.get("source") or {}).get("files", []) or []:
                target = base / "files" / short / str(entry.get("name", "rules")).replace("/", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(entry.get("content", "")), encoding="utf-8")
                files += 1
        progress("Security rules written", len(seen), len(seen))
        return SnapshotResult(self.key, "ok", files=files, counts={"rulesets": len(seen)})
