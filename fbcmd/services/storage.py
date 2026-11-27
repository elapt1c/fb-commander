"""Cloud Storage: bucket/prefix/object browsing, streaming downloads, mirror backup."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from ..models import Item, Preview, Probe, ServiceState, SnapshotResult, TablePage
from .base import Service, human_bytes, noop, safe_join, write_json

TEXTUAL_TYPES = ("text/", "application/json", "application/xml", "application/javascript")
SNIPPET_BYTES = 4096


class StorageService(Service):
    key = "storage"
    label = "Cloud Storage"
    icon = "▤"
    accent = "#79c0ff"
    blurb = "Buckets & files"

    # -- helpers -------------------------------------------------------
    def _api(self, path: str = "") -> str:
        return f"{self.endpoints.storage}/storage/v1" + path

    def _object_url(self, bucket: str, name: str) -> str:
        return self._api(f"/b/{quote(bucket, safe='')}/o/{quote(name, safe='')}")

    def _list_buckets(self) -> list[dict]:
        data = self.api.get_json(self._api("/b"), params={"project": self.project, "maxResults": 1000})
        return data.get("items", []) or []

    @staticmethod
    def parse(path: str) -> tuple[str, str]:
        """``b:<bucket>/<prefix>`` -> (bucket, prefix); ``''`` means the root."""
        if not path.startswith("b:"):
            return "", ""
        rest = path[2:]
        bucket, _, prefix = rest.partition("/")
        return bucket, prefix

    @staticmethod
    def path_for(bucket: str, prefix: str = "") -> str:
        return f"b:{bucket}" + (f"/{prefix}" if prefix else "")

    # -- service -------------------------------------------------------
    def probe(self) -> Probe:
        buckets = self._list_buckets()
        return Probe(ServiceState.OK, f"{len(buckets)} bucket(s)", count=len(buckets))

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        bucket, prefix = self.parse(path)
        if not bucket:
            rows = []
            items = []
            for entry in self._list_buckets():
                name = str(entry.get("name", ""))
                rows.append(
                    [
                        name,
                        str(entry.get("location", "—")),
                        str(entry.get("storageClass", "—")),
                        str(entry.get("timeCreated", "—"))[:19].replace("T", " "),
                        "bucket",
                    ]
                )
                items.append(Item(key=self.path_for(name), label=name, kind="bucket", leaf=False, detail=str(entry.get("location", ""))))
            return TablePage(
                title="Buckets",
                columns=["BUCKET", "LOCATION", "CLASS", "CREATED", "TYPE"],
                rows=rows,
                items=items,
                breadcrumbs=[("Buckets", "")],
                subtitle=f"{len(rows)} bucket(s) in {self.project}",
            )

        params = {"prefix": prefix, "delimiter": "/", "pageSize": 200}
        if token:
            params["pageToken"] = token
        data = self.api.get_json(self._api(f"/b/{quote(bucket, safe='')}/o"), params=params)
        rows: list[list[str]] = []
        items: list[Item] = []
        for folder in sorted(data.get("prefixes", []) or []):
            rows.append([folder, "—", "—", "—", "folder"])
            items.append(Item(key=json.dumps(["p", bucket, folder]), label=folder, kind="prefix", leaf=False, detail="folder"))
        for obj in data.get("items", []) or []:
            name = str(obj.get("name", ""))
            if name == prefix:  # the "folder placeholder" object
                continue
            size = int(obj.get("size", 0) or 0)
            rows.append(
                [
                    name,
                    human_bytes(size),
                    str(obj.get("contentType", "—")),
                    str(obj.get("updated", "—"))[:19].replace("T", " "),
                    "object",
                ]
            )
            items.append(
                Item(
                    key=json.dumps(["o", bucket, name]),
                    label=name.rsplit("/", 1)[-1],
                    kind="object",
                    leaf=True,
                    detail=human_bytes(size),
                    meta={"bucket": bucket, "name": name, "size": size, "contentType": obj.get("contentType", "")},
                )
            )
        crumbs = [("Buckets", ""), (bucket, self.path_for(bucket))]
        parts = prefix.rstrip("/").split("/") if prefix else []
        for index, part in enumerate(parts):
            crumbs.append((part, self.path_for(bucket, "/".join(parts[: index + 1]) + "/")))
        return TablePage(
            title=f"{bucket}/{prefix}",
            columns=["OBJECT", "SIZE", "TYPE", "UPDATED", "KIND"],
            rows=rows,
            items=items,
            breadcrumbs=crumbs,
            next_token=data.get("nextPageToken"),
            subtitle=f"{len(items)} entr{'y' if len(items) == 1 else 'ies'} · prefix {prefix or '/'}",
        )

    def preview(self, item: Item) -> Preview:
        if item.kind != "object":
            return Preview(title=item.label, body=json.dumps({"bucket": item.meta.get("bucket"), "path": item.key}, indent=2))
        bucket = item.meta["bucket"]
        name = item.meta["name"]
        meta = self.api.get_json(self._object_url(bucket, name))
        body = json.dumps(meta, indent=2, ensure_ascii=False, default=str)
        content_type = str(meta.get("contentType", ""))
        if content_type.startswith(TEXTUAL_TYPES) and int(meta.get("size", 0) or 0) <= 512 * 1024:
            try:
                response = self.api.request(
                    "GET",
                    self._object_url(bucket, name),
                    params={"alt": "media"},
                    headers={"Range": f"bytes=0-{SNIPPET_BYTES - 1}"},
                )
                snippet = response.text[:SNIPPET_BYTES]
                response.close()
                body += "\n\n// ---- first bytes ----\n" + snippet
            except Exception:  # noqa: BLE001 - preview must never break browsing
                pass
        return Preview(
            title=f"{bucket}/{name}",
            body=body,
            meta=[
                ("bucket", bucket),
                ("size", human_bytes(int(meta.get("size", 0) or 0))),
                ("type", content_type or "—"),
                ("md5", str(meta.get("md5Hash", "—"))),
                ("updated", str(meta.get("updated", "—"))),
            ],
        )

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        if item.kind != "object":
            raise ValueError("only objects can be downloaded individually")
        bucket = item.meta["bucket"]
        name = item.meta["name"]
        target = dest
        target.parent.mkdir(parents=True, exist_ok=True)
        progress(f"Downloading {name}", 0, item.meta.get("size"))
        written = self.api.download(self._object_url(bucket, name), target, params={"alt": "media"})
        progress(f"Downloaded {human_bytes(written)}", written, written)
        return target

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        prefix_filter = getattr(plan, "storage_prefix", "") or ""
        limit = int(getattr(plan, "max_file_bytes", 0) or 0)
        base = dest / "storage"
        files = 0
        total_bytes = 0
        skipped: list[dict] = []
        buckets = self._list_buckets()
        for bucket_entry in buckets:
            bucket = str(bucket_entry.get("name", ""))
            token: str | None = None
            while True:
                params = {"prefix": prefix_filter, "pageSize": 1000}
                if token:
                    params["pageToken"] = token
                data = self.api.get_json(self._api(f"/b/{quote(bucket, safe='')}/o"), params=params)
                for obj in data.get("items", []) or []:
                    name = str(obj.get("name", ""))
                    size = int(obj.get("size", 0) or 0)
                    if limit and size > limit:
                        skipped.append({"bucket": bucket, "name": name, "size": size, "reason": "over size limit"})
                        progress(f"skipped {name} ({human_bytes(size)})", files, None)
                        continue
                    target = safe_join(base / bucket, name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    progress(f"{bucket}/{name}", files, None)
                    written = self.api.download(self._object_url(bucket, name), target, params={"alt": "media"})
                    files += 1
                    total_bytes += written
                token = data.get("nextPageToken")
                if not token:
                    break
        write_json(
            base / "_buckets.json",
            {
                "buckets": [b.get("name") for b in buckets],
                "prefix": prefix_filter,
                "files": files,
                "bytes": total_bytes,
                "skipped": skipped,
            },
        )
        progress("Storage mirrored", files, files)
        return SnapshotResult(
            self.key,
            "ok",
            files=files + 1,
            bytes=total_bytes,
            counts={"buckets": len(buckets), "objects": files, "skipped": len(skipped)},
        )
