"""Firebase Hosting: sites, channels, releases (metadata; no file mirroring)."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Item, Preview, Probe, ServiceState, SnapshotResult, TablePage
from .base import Ctx, Service, noop, write_json


class HostingService(Service):
    key = "hosting"
    label = "Hosting"
    icon = "◫"
    accent = "#ff9e64"
    blurb = "Sites & releases"

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.base = f"{self.endpoints.hosting}/v1beta1"

    def _sites(self) -> list[dict]:
        data = self.api.get_json(f"{self.base}/sites", params={"parent": f"projects/{self.project}", "pageSize": 100})
        return data.get("sites", []) or []

    def _site_name(self, site: str) -> str:
        return site if site.startswith("projects/") else f"projects/{self.project}/sites/{site}"

    def probe(self) -> Probe:
        sites = self._sites()
        return Probe(ServiceState.OK, f"{len(sites)} site(s)", count=len(sites))

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        if path.startswith("s:"):
            site = path[2:]
            name = self._site_name(site)
            releases = self.api.get_json(f"{self.base}/{name}/releases", params={"pageSize": 50}).get("releases", []) or []
            rows = []
            items = []
            for rel in releases:
                version = str(rel.get("version", {}).get("name", rel.get("name", "")))
                kind = str(rel.get("type", "DEPLOY"))
                rows.append(
                    [
                        version.rsplit("/", 1)[-1],
                        kind,
                        str(rel.get("releaseTime", "—"))[:19].replace("T", " "),
                        str(rel.get("message", "") or "—")[:50],
                    ]
                )
                items.append(Item(key=json.dumps([site, version]), label=version.rsplit("/", 1)[-1], kind="release", meta=rel))
            return TablePage(
                title=f"{site} · releases",
                columns=["VERSION", "TYPE", "RELEASED", "MESSAGE"],
                rows=rows,
                items=items,
                breadcrumbs=[("Sites", ""), (site, f"s:{site}")],
                subtitle=f"{len(rows)} release(s)",
                note="Metadata only — Hosting file contents are not mirrored by this tool.",
            )

        rows = []
        items = []
        for site in self._sites():
            name = str(site.get("name", ""))
            short = name.rsplit("/", 1)[-1]
            rows.append(
                [
                    short,
                    str(site.get("defaultUrl", "—")),
                    str(site.get("type", "DEFAULT")),
                    str(site.get("appId", "—")),
                ]
            )
            items.append(Item(key=f"s:{short}", label=short, kind="site", leaf=False, detail=str(site.get("defaultUrl", "")), meta=site))
        return TablePage(
            title="Sites",
            columns=["SITE", "DEFAULT URL", "TYPE", "APP"],
            rows=rows,
            items=items,
            breadcrumbs=[("Sites", "")],
            subtitle=f"{len(rows)} hosting site(s)",
        )

    def preview(self, item: Item) -> Preview:
        if item.kind == "release":
            return Preview(title=item.label, body=json.dumps(item.meta, indent=2, ensure_ascii=False, default=str))
        site = item.meta
        name = self._site_name(str(site.get("name", item.key[2:])).rsplit("/", 1)[-1])
        try:
            channels = self.api.get_json(f"{self.base}/{name}/channels", params={"pageSize": 50}).get("channels", []) or []
        except Exception:  # noqa: BLE001 - channels are a bonus, not required
            channels = []
        body = json.dumps({"site": site, "channels": channels}, indent=2, ensure_ascii=False, default=str)
        return Preview(title=item.label, body=body)

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        target = dest if dest.suffix.lower() == ".json" else dest.with_suffix(".json")
        write_json(target, item.meta or {}, indent=2)
        return target

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        base = dest / "hosting"
        sites = self._sites()
        write_json(base / "sites.json", sites)
        files = 1
        for site in sites:
            short = str(site.get("name", "")).rsplit("/", 1)[-1]
            name = self._site_name(short)
            progress(f"hosting · {short}", None, None)
            site_dir = base / short
            try:
                releases = self.api.get_json(f"{self.base}/{name}/releases", params={"pageSize": 100}).get("releases", []) or []
            except Exception as exc:  # noqa: BLE001 - record and continue
                write_json(site_dir / "_error.json", {"error": str(exc)})
                continue
            channels = []
            try:
                channels = self.api.get_json(f"{self.base}/{name}/channels", params={"pageSize": 100}).get("channels", []) or []
            except Exception:  # noqa: BLE001 - channels optional
                pass
            write_json(site_dir / "releases.json", releases)
            write_json(site_dir / "channels.json", channels)
            write_json(site_dir / "site.json", site)
            files += 3
        progress("Hosting metadata written", len(sites), len(sites))
        return SnapshotResult(self.key, "ok", files=files, counts={"sites": len(sites)})
