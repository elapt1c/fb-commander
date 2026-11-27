"""Backup orchestration: plan -> folder layout -> manifest."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import ServiceState, SnapshotResult
from .session import Session

README = """fb-commander backup
===================

project/       Firebase project metadata, registered apps, enabled APIs
firestore/     one JSON file per document, subcollections nested under the doc
rtdb/          the whole Realtime Database (or the subtree you selected)
auth/          users.ndjson (one user per line) + users.json
storage/       a byte-for-byte mirror of each bucket (prefix filter respected)
remoteconfig/  full config + one file per parameter
hosting/       sites, channels and releases (metadata only)
rules/         live ruleset releases and their source files

manifest.json  what ran, what was skipped, whether access was denied, and counts
"""


@dataclass
class BackupPlan:
    dest: Path
    services: list[str] = field(default_factory=list)
    rtdb_path: str = "/"
    storage_prefix: str = ""
    max_file_bytes: int = 0
    label: str = ""
    cancel: threading.Event | None = None


@dataclass
class BackupReport:
    root: Path
    manifest: Path
    results: list[SnapshotResult]

    @property
    def total_files(self) -> int:
        return sum(r.files for r in self.results)

    @property
    def total_bytes(self) -> int:
        return sum(r.bytes for r in self.results)

    @property
    def failed(self) -> list[SnapshotResult]:
        return [r for r in self.results if r.state == "error"]

    @property
    def skipped(self) -> list[SnapshotResult]:
        return [r for r in self.results if r.state in ("denied", "disabled", "empty", "cancelled")]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def plan_root(dest: Path, project: str) -> Path:
    root = Path(dest).expanduser() / f"{project}-{_stamp()}"
    suffix = 1
    candidate = root
    while candidate.exists():
        candidate = root.with_name(f"{root.name}-{suffix}")
        suffix += 1
    return candidate


def run_backup(
    session: Session,
    plan: BackupPlan,
    progress: Callable[[str, str, int | None, int | None], None] = lambda *a: None,
) -> BackupReport:
    root = plan_root(plan.dest, session.project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.txt").write_text(README, encoding="utf-8")

    started = datetime.now(timezone.utc)
    manifest_path = root / "manifest.json"
    manifest = {
        "tool": "fb-commander/1.0",
        "project": session.project_id,
        "client_email": session.account.client_email,
        "key_id": session.account.private_key_id,
        "started_at": started.isoformat(),
        "options": {
            "services": plan.services,
            "rtdb_path": plan.rtdb_path,
            "storage_prefix": plan.storage_prefix,
            "max_file_bytes": plan.max_file_bytes,
            "label": plan.label,
        },
        "services": [],
        "totals": {},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    results: list[SnapshotResult] = []
    for index, key in enumerate(plan.services, 1):
        service = session.services.get(key)
        probe = session.probes.get(key)
        if service is None:
            continue
        if probe is not None and not probe.state.usable:
            result = SnapshotResult(
                key,
                probe.state.value,
                error=f"skipped: {probe.state.value} ({probe.detail})",
            )
            progress(key, f"skipped — {probe.state.value}", index, len(plan.services))
        elif plan.cancel is not None and plan.cancel.is_set():
            result = SnapshotResult(key, "cancelled", error="cancelled before start")
        else:
            progress(key, "starting", index, len(plan.services))
            try:
                result = service.snapshot(
                    root,
                    plan,
                    lambda message, done=None, total=None, _key=key: progress(_key, message, done, total),
                )
            except Exception as exc:  # noqa: BLE001 - one service failing must not abort the backup
                result = SnapshotResult(key, "error", error=str(exc) or exc.__class__.__name__)
                progress(key, f"failed — {result.error[:120]}", index, len(plan.services))

        results.append(result)
        entry = {
            "key": result.service,
            "label": service.label,
            "state": result.state,
            "files": result.files,
            "bytes": result.bytes,
            "counts": result.counts,
        }
        if result.error:
            entry["error"] = result.error
        manifest["services"].append(entry)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["duration_s"] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
    manifest["totals"] = {
        "files": sum(r.files for r in results),
        "bytes": sum(r.bytes for r in results),
        "services_ok": sum(1 for r in results if r.state == "ok"),
        "services_skipped": sum(1 for r in results if r.state in ("denied", "disabled", "empty", "cancelled")),
        "services_failed": sum(1 for r in results if r.state == "error"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    progress("__done__", f"backup written to {root}", len(results), len(results))
    return BackupReport(root=root, manifest=manifest_path, results=results)


def probe_summary(session: Session) -> list[dict]:
    out = []
    for key, service in session.services.items():
        probe = session.probes.get(key)
        out.append(
            {
                "key": key,
                "label": service.label,
                "state": (probe.state.value if probe else ServiceState.CHECKING.value),
                "detail": (probe.detail if probe else ""),
            }
        )
    return out
