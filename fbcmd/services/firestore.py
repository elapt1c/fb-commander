"""Firestore: collections/documents tree, recursive export, JSON backup."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from ..errors import ApiError
from ..firestore_codec import decode_document, doc_id
from ..models import Item, Preview, Probe, ServiceState, SnapshotResult
from .base import Ctx, Service, noop, write_json

MAX_TREE_DOCS = 500
PAGE = 300


def _fname(name: str) -> str:
    cleaned = str(name).replace("/", "_").replace("\\", "_").replace("\x00", "").strip()
    if cleaned in ("", ".", ".."):
        cleaned = "_"
    return cleaned[:180]


class FirestoreService(Service):
    key = "firestore"
    label = "Firestore"
    icon = "▦"
    accent = "#ffc857"
    blurb = "Collections & documents"
    tree = True

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.db_path = f"projects/{self.project}/databases/(default)/documents"

    # -- urls ----------------------------------------------------------
    def _doc_url(self, ids: list[str]) -> str:
        path = "/".join(quote(str(part), safe="") for part in ids)
        return f"{self.endpoints.firestore}/v1/{self.db_path}" + (f"/{path}" if path else "")

    def _list_collections(self, ids: list[str]) -> list[str]:
        url = f"{self._doc_url(ids)}:listCollectionIds"
        out: list[str] = []
        token: str | None = None
        while True:
            body = {"pageSize": 1000}
            if token:
                body["pageToken"] = token
            data = self.api.post_json(url, body)
            out.extend(str(c) for c in data.get("collectionIds", []))
            token = data.get("nextPageToken")
            if not token or len(out) >= 5000:
                return out

    def _list_documents(self, ids: list[str], max_docs: int | None = None) -> list[dict]:
        url = self._doc_url(ids)
        docs: list[dict] = []
        token: str | None = None
        while True:
            data = self.api.get_json(url, params={"pageSize": PAGE, "pageToken": token})
            docs.extend(data.get("documents", []) or [])
            token = data.get("nextPageToken")
            if not token or (max_docs is not None and len(docs) >= max_docs):
                break
        return docs if max_docs is None else docs[:max_docs]

    # -- tree ----------------------------------------------------------
    def probe(self) -> Probe:
        try:
            ids = self._list_collections([])
        except ApiError as exc:
            if exc.kind == "not_found":
                return Probe(ServiceState.EMPTY, "database not created yet")
            raise
        return Probe(ServiceState.OK, f"{len(ids)} root collection(s)", count=len(ids))

    def tree_roots(self) -> list[Item]:
        return [
            Item(key=json.dumps([cid]), label=cid, kind="collection", leaf=False, detail="collection")
            for cid in sorted(self._list_collections([]), key=str.lower)
        ]

    def tree_children(self, item: Item) -> list[Item]:
        ids = json.loads(item.key)
        if len(ids) % 2 == 1:  # collection -> documents
            docs = self._list_documents(ids, max_docs=MAX_TREE_DOCS)
            items = []
            for doc in docs:
                did = doc_id(doc.get("name", ""))
                fields = doc.get("fields", {}) or {}
                names = ", ".join(list(fields)[:3]) if fields else "empty"
                if len(fields) > 3:
                    names += f", +{len(fields) - 3}"
                items.append(
                    Item(
                        key=json.dumps(ids + [did]),
                        label=did,
                        kind="doc",
                        leaf=False,
                        detail=names,
                    )
                )
            return items
        # document -> subcollections
        return [
            Item(
                key=json.dumps(ids + [sub]),
                label=sub,
                kind="collection",
                leaf=False,
                detail="subcollection",
            )
            for sub in sorted(self._list_collections(ids), key=str.lower)
        ]

    # -- preview -------------------------------------------------------
    def preview(self, item: Item) -> Preview:
        ids = json.loads(item.key)
        if len(ids) % 2 == 1:
            docs = self._list_documents(ids, max_docs=25)
            body = {
                "collection": ids[-1],
                "path": f"{self.db_path}/" + "/".join(ids),
                "documents_previewed": len(docs),
                "document_ids": [doc_id(d.get("name", "")) for d in docs],
                "hint": "expand the collection to browse documents",
            }
            return Preview(title=f"{ids[-1]} (collection)", body=json.dumps(body, indent=2, ensure_ascii=False))
        doc = self.api.get_json(self._doc_url(ids))
        return Preview(
            title="/".join(ids),
            body=json.dumps(decode_document(doc), indent=2, ensure_ascii=False),
            meta=[("document", ids[-1]), ("collection", ids[-2] if len(ids) > 1 else "/")],
        )

    # -- download ------------------------------------------------------
    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        ids = json.loads(item.key)
        if len(ids) % 2 == 1:
            target = dest if dest.suffix == "" else dest.with_suffix("")
            counts = {"collections": 0, "documents": 0}
            progress(f"Exporting {ids[-1]}", 0, None)
            self.export_collection(ids, target, progress, counters=counts)
            progress("Collection exported", counts["documents"], counts["documents"])
            write_json(target / "_collection.json", {"path": "/".join(ids), **counts})
            return target
        doc = self.api.get_json(self._doc_url(ids))
        target = dest if dest.suffix.lower() == ".json" else dest.with_suffix(".json")
        write_json(target, decode_document(doc))
        return target

    def export_collection(
        self,
        ids: list[str],
        dest: Path,
        progress=noop,
        *,
        depth: int = 0,
        counters: dict | None = None,
    ) -> None:
        counters = counters if counters is not None else {"collections": 0, "documents": 0}
        dest.mkdir(parents=True, exist_ok=True)
        if depth == 0:
            counters["collections"] += 1
        docs = self._list_documents(ids)
        total = len(docs)
        for index, doc in enumerate(docs, 1):
            did = doc_id(doc.get("name", ""))
            write_json(dest / f"{_fname(did)}.json", decode_document(doc))
            counters["documents"] += 1
            progress(f"/{'/'.join(ids)} · {did}", index, total)
            if depth >= 15:
                continue
            for sub in sorted(self._list_collections(ids + [did])):
                counters["collections"] += 1
                self.export_collection(
                    ids + [did, sub],
                    dest / _fname(did) / _fname(sub),
                    progress,
                    depth=depth + 1,
                    counters=counters,
                )

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        roots = sorted(self._list_collections([]), key=str.lower)
        counters = {"collections": 0, "documents": 0}
        base = dest / "firestore"
        files = 0
        for index, cid in enumerate(roots, 1):
            progress(f"collection {cid}", index, len(roots))
            start = counters["documents"]
            self.export_collection([cid], base / _fname(cid), progress, counters=counters)
            files += 1 + (counters["documents"] - start)
        write_json(
            base / "_collections.json",
            {"collections": roots, "documents": counters["documents"]},
        )
        progress("Firestore written", len(roots), len(roots))
        return SnapshotResult(
            self.key,
            "ok",
            files=files + 1,
            counts={"collections": len(roots), "documents": counters["documents"]},
        )
