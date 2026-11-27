"""Firebase Authentication user directory: browse, search, export."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import Item, Preview, Probe, ServiceState, SnapshotResult, TablePage
from .base import Ctx, Service, noop, write_json

PAGE = 100


def _ts(value) -> str:
    if value in (None, "", 0, "0"):
        return "—"
    try:
        stamp = int(value) / 1000.0
    except (TypeError, ValueError):
        return str(value)[:19]
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _provider(user: dict) -> str:
    providers = [str(p.get("providerId", "")) for p in user.get("providerInfo", []) if p.get("providerId")]
    if not providers:
        providers = [str(user.get("providerId", ""))] if user.get("providerId") else []
    short = {"password": "password", "google.com": "google", "apple.com": "apple", "facebook.com": "facebook"}
    return ",".join(short.get(p, p.split(".")[0]) for p in providers) or "—"


class IdentityService(Service):
    key = "auth"
    label = "Authentication"
    icon = "◉"
    accent = "#7ee787"
    blurb = "Users & providers"

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.url = f"{self.endpoints.identity}/v1/projects/{self.project}/accounts:query"
        self.lookup_url = f"{self.endpoints.identity}/v1/projects/{self.project}/accounts:lookup"

    def _query(self, token: str | None = None, limit: int = PAGE):
        body: dict = {"maxResults": limit}
        if token:
            body["nextPageToken"] = token
        return self.api.post_json(self.url, body)

    def probe(self) -> Probe:
        data = self._query(limit=1)
        return Probe(ServiceState.OK, "user directory reachable", count=None) if data is not None else Probe(
            ServiceState.EMPTY, "no users"
        )

    def table(self, path: str = "", token: str | None = None, query: str = "") -> TablePage:
        if query:
            if "@" in query:
                data = self.api.post_json(self.lookup_url, {"email": [query]})
            else:
                data = self.api.post_json(self.lookup_url, {"localId": [query]})
            users = data.get("users", []) or []
            token = None
        else:
            data = self._query(token=token)
            users = data.get("users", []) or []
            token = data.get("nextPageToken")
        rows = []
        items = []
        for user in users:
            uid = str(user.get("localId", ""))
            email = str(user.get("email") or user.get("phoneNumber") or "—")
            rows.append(
                [
                    email,
                    uid,
                    _provider(user),
                    _ts(user.get("createdAt")),
                    _ts(user.get("lastLoginAt")),
                    "disabled" if user.get("disabled") else ("unverified" if user.get("emailVerified") is False else "active"),
                ]
            )
            items.append(Item(key=uid, label=email, kind="user", leaf=True, detail=uid, meta=user))
        return TablePage(
            title="Users",
            columns=["EMAIL", "UID", "PROVIDERS", "CREATED", "LAST SIGN-IN", "STATE"],
            rows=rows,
            items=items,
            breadcrumbs=[("Users", "")],
            next_token=token,
            subtitle=f"{len(rows)} user(s) on this page" + (" · filtered" if query else ""),
        )

    def preview(self, item: Item) -> Preview:
        user = item.meta or self.api.post_json(self.lookup_url, {"localId": [item.key]}).get("users", [{}])[0]
        body = json.dumps(user, indent=2, ensure_ascii=False, default=str)
        meta = [
            ("uid", str(user.get("localId", item.key))),
            ("email", str(user.get("email") or "—")),
            ("created", _ts(user.get("createdAt"))),
            ("last sign-in", _ts(user.get("lastLoginAt"))),
            ("disabled", str(bool(user.get("disabled")))),
        ]
        return Preview(title=f"user · {user.get('email') or item.key}", body=body, meta=meta)

    def download(self, item: Item, dest: Path, progress=noop) -> Path:
        user = item.meta or self.api.post_json(self.lookup_url, {"localId": [item.key]}).get("users", [{}])[0]
        target = dest if dest.suffix.lower() == ".json" else dest.with_suffix(".json")
        write_json(target, user)
        return target

    def snapshot(self, dest: Path, plan, progress=noop) -> SnapshotResult:
        base = dest / "auth"
        base.mkdir(parents=True, exist_ok=True)
        token: str | None = None
        count = 0
        ndjson = base / "users.ndjson"
        array = base / "users.json"
        with ndjson.open("w", encoding="utf-8") as stream, array.open("w", encoding="utf-8") as arr:
            arr.write("[\n")
            first = True
            while True:
                data = self._query(token=token)
                users = data.get("users", []) or []
                for user in users:
                    stream.write(json.dumps(user, ensure_ascii=False, default=str) + "\n")
                    arr.write(("" if first else ",\n") + json.dumps(user, ensure_ascii=False, default=str))
                    first = False
                    count += 1
                progress(f"auth users · {count}", count, None)
                token = data.get("nextPageToken")
                if not token or not users:
                    break
            arr.write("\n]\n")
        write_json(base / "_meta.json", {"users": count, "pages": "all", "note": "password hashes only export on Identity Platform (GCIP) projects"})
        progress("Auth users written", count, count)
        return SnapshotResult(self.key, "ok", files=3, bytes=ndjson.stat().st_size + array.stat().st_size, counts={"users": count})
