"""A tiny in-process Firebase REST fixture server.

Not a Firebase emulator — just enough of the REST surface (RTDB, Firestore,
Auth, Storage, Remote Config, Hosting, Rules, project metadata) to drive the
whole tool offline: discovery, lazy trees, previews, downloads and backups.
Access denials are toggleable at runtime via POST /__control.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

PROJECT = "demo-project"
DB = f"projects/{PROJECT}/databases/(default)/documents"


def _str(value):
    return {"stringValue": value}


def _int(value):
    return {"integerValue": str(value)}


def _doc(*fields):
    return {"fields": {k: v for k, v in fields}}


RTDB = {
    "config": {"theme": "dark", "maxItems": 10},
    "users": {"u1": {"name": "Ada", "age": 36}, "u2": {"name": "Grace", "age": 41}},
    "posts": {"p1": {"title": "hello", "tags": ["a", "b"]}},
}

FS_ROOT = {
    "users": {
        "u1": {
            "fields": {"name": _str("Ada"), "age": _int(36)},
            "sub": {"orders": {"o1": {"fields": {"total": _int(20), "sku": _str("A-1")}}}},
        },
        "u2": {"fields": {"name": _str("Grace"), "age": _int(41)}, "sub": {}},
    },
    "posts": {"p1": {"fields": {"title": _str("hello"), "published": {"booleanValue": True}}, "sub": {}}},
}

USERS = [
    {
        "localId": "uid-001",
        "email": "ada@example.com",
        "emailVerified": True,
        "displayName": "Ada",
        "createdAt": "1700000000000",
        "lastLoginAt": "1730000000000",
        "providerInfo": [{"providerId": "password", "email": "ada@example.com"}],
    },
    {
        "localId": "uid-002",
        "email": "grace@example.com",
        "emailVerified": False,
        "createdAt": "1700000100000",
        "lastLoginAt": "1730000100000",
        "disabled": True,
        "providerInfo": [{"providerId": "google.com", "email": "grace@example.com"}],
    },
    {
        "localId": "uid-003",
        "email": "linus@example.com",
        "emailVerified": True,
        "createdAt": "1700000200000",
        "providerInfo": [{"providerId": "password", "email": "linus@example.com"}],
    },
]

BUCKET = "demo-project.appspot.com"
OBJECTS = {
    "readme.txt": (b"hello backup\n", "text/plain"),
    "photos/a.jpg": (b"\xff\xd8fakejpg", "image/jpeg"),
    "photos/b.jpg": (b"\xff\xd8fakejpg2", "image/jpeg"),
    "data/export.json": (b'{"ok": true}', "application/json"),
}

REMOTE_CONFIG = {
    "conditions": [{"name": "ios_users", "expression": "device.os == 'ios'"}],
    "parameters": {
        "welcome_banner": {
            "defaultValue": {"value": "Welcome"},
            "description": "Home banner copy",
            "valueType": "STRING",
        },
        "max_items": {"defaultValue": {"value": "10"}, "valueType": "NUMBER"},
        "beta": {
            "defaultValue": {"value": "false"},
            "valueType": "BOOLEAN",
            "conditionalValues": {"ios_users": {"value": "true"}},
        },
    },
    "version": {"versionNumber": "7", "updateTime": "2024-01-01T00:00:00Z"},
    "etag": "etag-1234567890",
}

RULES_RELEASE = {
    "name": f"projects/{PROJECT}/releases/cloud.firestore",
    "rulesetName": f"projects/{PROJECT}/rulesets/rset-abc",
    "updateTime": "2024-02-02T10:00:00Z",
}
RULESET = {
    "name": f"projects/{PROJECT}/rulesets/rset-abc",
    "createTime": "2024-02-02T09:59:00Z",
    "source": {
        "files": [
            {
                "name": "firestore.rules",
                "content": "rules_version = '2';\nservice cloud.firestore {\n  match /databases/{db}/documents {\n    allow read: if false;\n  }\n}\n",
            }
        ]
    },
}

PROJECT_INFO = {
    "projectId": PROJECT,
    "projectNumber": "123456789",
    "displayName": "Demo Project",
    "state": "ACTIVE",
    "resources": {
        "locationId": "us-central",
        "realtimeDatabaseInstance": f"{PROJECT}-default-rtdb",
        "storageBucket": BUCKET,
        "hostingSite": PROJECT,
    },
}


class State:
    def __init__(self, deny: set[str] | None = None):
        self.deny = set(deny or set())
        self.requests: list[str] = []
        self.lock = threading.Lock()


def _fs_children(ids: list[str]) -> dict:
    node = FS_ROOT
    index = 0
    while index + 1 < len(ids):
        doc = node.get(ids[index], {}).get(ids[index + 1], {})
        node = doc.get("sub", {})
        index += 2
    return node


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # silence
            return

        # -- plumbing --------------------------------------------------
        def _send(self, payload, status=200, content_type="application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def _error(self, status, message, reason=""):
            self._send({"error": {"code": status, "message": message, "status": reason or "ERROR"}}, status)

        def _authorized(self):
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                self._error(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED")
                return False
            return True

        def _denied(self, service: str) -> bool:
            if service in state.deny:
                self._error(403, f"Permission denied on {service}.", "PERMISSION_DENIED")
                return True
            return False

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            url = urlparse(self.path)
            path = unquote(url.path)
            if path == "/__control":
                if "deny" in body:
                    state.deny = set(body["deny"])
                self._send({"deny": sorted(state.deny)})
                return
            if not self._authorized():
                return

            # identity toolkit
            if path.endswith("/accounts:query"):
                if self._denied("auth"):
                    return
                limit = min(int(body.get("maxResults") or 100), 2)  # force paging in tests
                start = int(body.get("nextPageToken") or 0)
                page = USERS[start : start + limit]
                out = {"users": page, "kind": "identitytoolkit#GetAccountInfoResponse"}
                if start + limit < len(USERS):
                    out["nextPageToken"] = str(start + limit)
                self._send(out)
                return
            if path.endswith("/accounts:lookup"):
                if self._denied("auth"):
                    return
                emails = set(body.get("email") or [])
                ids = set(body.get("localId") or [])
                found = [u for u in USERS if u["email"] in emails or u["localId"] in ids]
                self._send({"users": found, "kind": "identitytoolkit#GetAccountInfoResponse"})
                return

            # firestore listCollectionIds
            if path.endswith(":listCollectionIds"):
                if self._denied("firestore"):
                    return
                prefix = f"/v1/{DB}"
                parent = path[: -len(":listCollectionIds")]
                ids = [p for p in parent[len(prefix) :].split("/") if p]
                if len(ids) % 2 != 0:
                    self._error(400, "parent must be a document path")
                    return
                self._send({"collectionIds": sorted(_fs_children(ids).keys())})
                return

            self._error(404, f"no fixture route for POST {path}", "NOT_FOUND")

        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            with state.lock:
                state.requests.append(f"{path}?{url.query}")
            if path == "/__health":
                self._send({"ok": True})
                return
            if not self._authorized():
                return

            # -- RTDB -------------------------------------------------
            if path.startswith("/rtdb/"):
                if self._denied("rtdb"):
                    return
                rel = path[len("/rtdb/") :]
                if not rel.endswith(".json"):
                    self._error(404, "expected .json")
                    return
                parts = [p for p in rel[: -len(".json")].split("/") if p]
                node = RTDB
                for part in parts:
                    if not isinstance(node, dict) or part not in node:
                        self._send({"error": "not found"}, 404)
                        return
                    node = node[part]
                if query.get("shallow") == "true" and isinstance(node, dict):
                    keys = list(node.keys())
                    limit = int(query.get("limitToFirst") or len(keys) or 1)
                    self._send({k: True for k in keys[:limit]})
                else:
                    self._send(node)
                return

            # -- Firestore -------------------------------------------
            if path.startswith(f"/v1/{DB}"):
                if self._denied("firestore"):
                    return
                ids = [p for p in path[len(f"/v1/{DB}") :].split("/") if p]
                if not ids:
                    self._send({"documents": [], "collectionIds": sorted(FS_ROOT)})
                    return
                if len(ids) % 2 == 1:  # collection -> documents
                    docs_map = self._fs_docs_for(ids)
                    page_size = int(query.get("pageSize") or 300)
                    start = int(query.get("pageToken") or 0)
                    names = sorted(docs_map)
                    page = names[start : start + page_size]
                    documents = []
                    for name in page:
                        doc = docs_map[name]
                        documents.append(
                            {
                                "name": f"{DB}/{'/'.join(ids)}/{name}",
                                "fields": doc["fields"],
                                "createTime": "2024-01-01T00:00:00Z",
                                "updateTime": "2024-01-02T00:00:00Z",
                            }
                        )
                    out = {"documents": documents}
                    if start + page_size < len(names):
                        out["nextPageToken"] = str(start + page_size)
                    self._send(out)
                    return
                # document path
                docs_map = self._fs_docs_for(ids[:-1])
                doc = docs_map.get(ids[-1])
                if doc is None:
                    self._error(404, "document not found")
                    return
                self._send(
                    {
                        "name": f"{DB}/{'/'.join(ids)}",
                        "fields": doc["fields"],
                        "createTime": "2024-01-01T00:00:00Z",
                        "updateTime": "2024-01-02T00:00:00Z",
                    }
                )
                return
            if path.startswith("/v1/projects/") and "/databases/(default)/documents" in path:
                if self._denied("firestore"):
                    return
                self._send({"documents": [], "collectionIds": sorted(FS_ROOT)})
                return

            # -- project / RTDB discovery -----------------------------
            if path.startswith("/v1beta1/projects/") and path.endswith("/webApps"):
                self._send({"apps": [{"name": f"projects/{PROJECT}/webApps/1:web:abc", "appId": "1:web:abc", "displayName": "Web app"}]})
                return
            if path.startswith("/v1beta1/projects/") and (path.endswith("/androidApps") or path.endswith("/iosApps")):
                self._send({"apps": []})
                return
            if path.startswith("/v1beta1/projects/"):
                if self._denied("project"):
                    return
                self._send(PROJECT_INFO)
                return
            if path.startswith("/v1beta/projects/"):
                if self._denied("rtdb"):
                    return
                port = self.server.server_address[1]
                self._send(
                    {
                        "instances": [
                            {
                                "name": f"projects/{PROJECT}/locations/us-central1/instances/{PROJECT}-default-rtdb",
                                "databaseUrl": f"http://127.0.0.1:{port}/rtdb",
                                "state": "ACTIVE",
                            }
                        ]
                    }
                )
                return
            if path.startswith("/v1/projects/") and path.endswith("/services"):
                if self._denied("serviceusage"):
                    return
                self._send(
                    {
                        "services": [
                            {"config": {"name": "firestore.googleapis.com"}, "state": "ENABLED"},
                            {"config": {"name": "identitytoolkit.googleapis.com"}, "state": "ENABLED"},
                        ]
                    }
                )
                return

            # -- storage ---------------------------------------------
            if path == "/storage/v1/b":
                if self._denied("storage"):
                    return
                self._send(
                    {
                        "items": [
                            {
                                "name": BUCKET,
                                "location": "US",
                                "storageClass": "STANDARD",
                                "timeCreated": "2024-01-01T00:00:00Z",
                            }
                        ]
                    }
                )
                return
            if path.startswith("/storage/v1/b/"):
                if self._denied("storage"):
                    return
                rest = path[len("/storage/v1/b/") :]
                bucket, _, tail = rest.partition("/")
                if tail == "":
                    self._send({"name": bucket, "location": "US"})
                    return
                if tail == "o":
                    prefix = query.get("prefix", "")
                    delimiter = query.get("delimiter", "")
                    page_size = int(query.get("pageSize") or 1000)
                    start = int(query.get("pageToken") or 0)
                    names = sorted(n for n in OBJECTS if n.startswith(prefix))
                    prefixes = set()
                    if delimiter:
                        trimmed = []
                        for name in names:
                            remainder = name[len(prefix) :]
                            if delimiter in remainder:
                                prefixes.add(prefix + remainder.split(delimiter)[0] + delimiter)
                            else:
                                trimmed.append(name)
                        names = trimmed
                    page = names[start : start + page_size]
                    items = []
                    for name in page:
                        body, ctype = OBJECTS[name]
                        items.append(
                            {
                                "name": name,
                                "size": str(len(body)),
                                "contentType": ctype,
                                "updated": "2024-01-03T00:00:00Z",
                                "md5Hash": "deadbeef",
                            }
                        )
                    out = {"items": items}
                    if prefixes:
                        out["prefixes"] = sorted(prefixes)
                    if start + page_size < len(names):
                        out["nextPageToken"] = str(start + page_size)
                    self._send(out)
                    return
                if tail.startswith("o/"):
                    name = unquote(tail[2:])
                    if name not in OBJECTS:
                        self._error(404, "object not found")
                        return
                    body, ctype = OBJECTS[name]
                    if query.get("alt") == "media":
                        self._send(body, content_type=ctype)
                    else:
                        self._send(
                            {
                                "name": name,
                                "bucket": bucket,
                                "size": str(len(body)),
                                "contentType": ctype,
                                "updated": "2024-01-03T00:00:00Z",
                                "md5Hash": "deadbeef",
                            }
                        )
                    return

            # -- remote config ---------------------------------------
            if path.endswith("/remoteConfig"):
                if self._denied("remoteconfig"):
                    return
                if "remoteconfig_missing" in state.deny:
                    self._error(404, "Remote Config has not been initialized")
                    return
                self._send(REMOTE_CONFIG)
                return

            # -- hosting ---------------------------------------------
            if path.startswith("/v1beta1/sites"):
                if self._denied("hosting"):
                    return
                self._send(
                    {
                        "sites": [
                            {
                                "name": f"projects/{PROJECT}/sites/{PROJECT}",
                                "defaultUrl": f"https://{PROJECT}.web.app",
                                "type": "DEFAULT",
                                "appId": "1:web:abc",
                            }
                        ]
                    }
                )
                return
            if path.endswith("/releases") and "/sites/" in path:
                self._send(
                    {
                        "releases": [
                            {
                                "name": f"projects/{PROJECT}/sites/{PROJECT}/releases/1700000000000",
                                "version": {"name": f"projects/{PROJECT}/sites/{PROJECT}/versions/v1"},
                                "type": "DEPLOY",
                                "releaseTime": "2024-01-05T10:00:00Z",
                                "message": "initial deploy",
                            }
                        ]
                    }
                )
                return
            if path.endswith("/channels") and "/sites/" in path:
                self._send({"channels": [{"name": f"projects/{PROJECT}/sites/{PROJECT}/channels/live", "url": f"https://{PROJECT}.web.app"}]})
                return

            # -- rules ------------------------------------------------
            if path.endswith("/releases") and path.startswith("/v1/projects/"):
                if self._denied("rules"):
                    return
                self._send({"releases": [RULES_RELEASE]})
                return
            if "/rulesets/" in path:
                if self._denied("rules"):
                    return
                self._send(RULESET)
                return

            self._error(404, f"no fixture route for GET {path}", "NOT_FOUND")

        @staticmethod
        def _fs_docs_for(ids: list[str]) -> dict:
            node = FS_ROOT
            index = 0
            while index + 1 < len(ids):
                node = node.get(ids[index], {}).get(ids[index + 1], {}).get("sub", {})
                index += 2
            return node.get(ids[index], {}) if index < len(ids) else {}

    return Handler


class MockFirebase:
    def __init__(self, deny: set[str] | None = None):
        self.state = State(deny)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> "MockFirebase":
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False

    def env(self) -> dict[str, str]:
        base = self.base
        return {
            "FB_CMD_ACCESS_TOKEN": "test-token",
            "FB_CMD_PROJECT_ID": PROJECT,
            "FB_CMD_FIREBASE_API": base,
            "FB_CMD_RTDB_ADMIN_API": base,
            "FB_CMD_FIRESTORE_API": base,
            "FB_CMD_IDENTITY_API": base,
            "FB_CMD_STORAGE_API": base,
            "FB_CMD_REMOTECONFIG_API": base,
            "FB_CMD_HOSTING_API": base,
            "FB_CMD_RULES_API": base,
            "FB_CMD_SERVICEUSAGE_API": base,
        }
