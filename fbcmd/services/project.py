"""Project overview: resources, apps, enabled APIs."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Item, Preview, Probe, ServiceState, SnapshotResult, TablePage
from .base import Ctx, Service, noop, write_json

PLATFORMS = (("webApps", "web"), ("androidApps", "android"), ("iosApps", "ios"))


class ProjectService(Service):
    key = "project"
    label = "Project"
    icon = "◆"
    accent = "#ffa000"
    blurb = "Resources & APIs"

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.base = f"{self.endpoints.firebase}/v1beta1/projects/{self.project}"
        self._project: dict | None = None

    def info(self, fresh: bool = False) -> dict:
        if self._project is None or fresh:
            self._project = self.api.get_json(self.base)
        return self._project

    def apps(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for key, label in PLATFORMS:
            try:
                out[label] = self.api.get_json(f"{self.base}/{key}").get("apps", []) or []
            except Exception as exc:  # noqa: BLE001 - per-platform permissions vary
                out[label] = [{"_error": str(exc)}]
        return out

    def apis(self) -> list[dict]:
        data = self.api.get_json(
            f"{self.endpoints.serviceusage}/v1/projects/{self.project}/services",
            params={"filter": "state:ENABLED", "pageSize": 300},
        )
        return data.get("services", []) or []

    # -- service -------------------------------------------------------
    def probe(self) -> Probe:
        project = self.info(fresh=True)
        name = str(project.get("displayName") or project.get("projectId") or self.project)
        return Probe(ServiceState.OK, name)

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        if path == "apps":
            apps = self.apps()
            rows = []
            items = []
            for platform, entries in apps.items():
                for app in entries:
                    if "_error" in app:
                        rows.append([platform, "—", f"denied: {str(app['_error'])[:60]}", "—"])
                        continue
                    rows.append(
                        [
                            platform,
                            str(app.get("appId", app.get("packageName", "—"))),
                            str(app.get("displayName", "—")),
                            str(app.get("namespace", app.get("bundleId", "—"))),
                        ]
                    )
                    items.append(Item(key=str(app.get("name", "")), label=str(app.get("displayName", "app")), kind="app", meta=app))
            return TablePage(
                title="Apps",
                columns=["PLATFORM", "ID", "NAME", "NAMESPACE"],
                rows=rows,
                items=items,
                breadcrumbs=[("Project", ""), ("Apps", "apps")],
                subtitle=f"{len(items)} registered app(s)",
            )
        if path == "apis":
            try:
                services = self.apis()
            except Exception as exc:  # noqa: BLE001 - needs serviceusage.services.list
                return TablePage(
                    title="Enabled APIs",
                    columns=["SERVICE", "STATE"],
                    rows=[["—", f"unavailable: {str(exc)[:80]}"]],
                    breadcrumbs=[("Project", ""), ("APIs", "apis")],
                    note="The key lacks serviceusage.services.list.",
                )
            rows = [[str(s.get("config", {}).get("name", s.get("name", ""))), str(s.get("state", ""))] for s in services]
            return TablePage(
                title="Enabled APIs",
                columns=["SERVICE", "STATE"],
                rows=rows,
                breadcrumbs=[("Project", ""), ("APIs", "apis")],
                subtitle=f"{len(rows)} enabled service(s)",
            )

        project = self.info()
        resources = project.get("resources", {}) or {}
        fields = [
            ("projectId", project.get("projectId")),
            ("projectNumber", project.get("projectNumber")),
            ("displayName", project.get("displayName")),
            ("state", project.get("state")),
            ("locationId", resources.get("locationId")),
            ("realtime database", resources.get("realtimeDatabaseInstance")),
            ("storage bucket", resources.get("storageBucket")),
            ("hosting site", resources.get("hostingSite")),
        ]
        rows = [[str(k), str(v if v not in (None, "") else "—")] for k, v in fields]
        return TablePage(
            title="Overview",
            columns=["FIELD", "VALUE"],
            rows=rows,
            items=[Item(key="overview", label="project", kind="overview", meta=project)],
            breadcrumbs=[("Project", "")],
            subtitle=f"projects/{self.project}",
            note="Press a to view registered apps · i for enabled APIs.",
        )

    def preview(self, item: Item) -> Preview:
        if item.kind == "app":
            return Preview(title=item.label, body=json.dumps(item.meta, indent=2, ensure_ascii=False, default=str))
        return Preview(title=f"project · {self.project}", body=json.dumps(self.info(), indent=2, ensure_ascii=False))

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        target = dest if dest.suffix.lower() == ".json" else dest.with_suffix(".json")
        write_json(target, item.meta if item.kind == "app" else self.info())
        return target

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        base = dest / "project"
        project = self.info(fresh=True)
        size = write_json(base / "project.json", project)
        apps = self.apps()
        write_json(base / "apps.json", apps)
        apis: list[dict] = []
        try:
            apis = self.apis()
        except Exception as exc:  # noqa: BLE001 - optional extra
            write_json(base / "apis.error.json", {"error": str(exc)})
        write_json(base / "enabled_apis.json", apis)
        progress("Project metadata written", 1, 1)
        return SnapshotResult(
            self.key,
            "ok",
            files=3 if apis else 2,
            bytes=size,
            counts={"apps": sum(len(v) for v in apps.values()), "apis": len(apis)},
        )
