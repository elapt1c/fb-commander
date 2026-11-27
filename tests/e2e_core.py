"""End-to-end test of the engine against the in-process Firebase fixture.

    .venv/bin/python tests/e2e_core.py

Covers: discovery, permission states, lazy trees, previews, pagination,
selective downloads and a full backup with manifest verification.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fbcmd.auth import Account  # noqa: E402
from fbcmd.backup import BackupPlan, run_backup  # noqa: E402
from fbcmd.config import Endpoints  # noqa: E402
from fbcmd.models import ServiceState  # noqa: E402
from fbcmd.session import Session  # noqa: E402
from tests.mock_firebase import PROJECT  # noqa: E402

CHECK = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global CHECK
    CHECK += 1
    if not condition:
        raise AssertionError(f"{label} failed{': ' + extra if extra else ''}")
    print(f"  ok  {label}{' — ' + extra if extra else ''}")


def fake_account() -> Account:
    info = {
        "type": "service_account",
        "project_id": PROJECT,
        "private_key_id": "fake-key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----\nRkFLRQ==\n-----END PRIVATE KEY-----\n",
        "client_email": f"firebase-adminsdk@ {PROJECT}.iam.gserviceaccount.com".replace(" ", ""),
    }
    return Account.from_text(json.dumps(info))


def connect(token: str = "test-token") -> Session:
    return Session.create(fake_account(), Endpoints(), static_token=token)


def test_probe_and_browse():
    from tests.mock_firebase import MockFirebase

    with MockFirebase(deny={"storage"}) as mock:
        os.environ.update(mock.env())
        session = connect()
        probes = session.probe_all()
        check("all services probed", len(probes) == len(session.services), f"{len(probes)} services")
        check("project is ok", probes["project"].state is ServiceState.OK, probes["project"].detail)
        check("firestore is ok", probes["firestore"].state is ServiceState.OK, probes["firestore"].detail)
        check("rtdb is ok", probes["rtdb"].state is ServiceState.OK, probes["rtdb"].detail)
        check("auth is ok", probes["auth"].state is ServiceState.OK, probes["auth"].detail)
        check("storage denied is greyed", probes["storage"].state is ServiceState.DENIED, probes["storage"].detail)
        check("rules is ok", probes["rules"].state is ServiceState.OK, probes["rules"].detail)

        # --- firestore tree
        fs = session.service("firestore")
        roots = fs.tree_roots()
        check("firestore roots", [r.label for r in roots] == ["posts", "users"], str([r.label for r in roots]))
        users = next(r for r in roots if r.label == "users")
        docs = fs.tree_children(users)
        check("collection has docs", [d.label for d in docs] == ["u1", "u2"], str([d.label for d in docs]))
        sub = fs.tree_children(docs[0])
        check("subcollection discovered", [s.label for s in sub] == ["orders"], str([s.label for s in sub]))
        orders = fs.tree_children(sub[0])
        check("nested doc discovered", [o.label for o in orders] == ["o1"])
        preview = fs.preview(orders[0])
        check("nested doc preview decodes", '"total": 20' in preview.body and '"/users/u1/orders/o1"' not in preview.body, preview.body[:80])

        # --- rtdb tree
        rtdb = session.service("rtdb")
        rroots = rtdb.tree_roots()
        check("rtdb roots", [r.label for r in rroots] == ["config", "posts", "users"], str([r.label for r in rroots]))
        config = next(r for r in rroots if r.label == "config")
        check("rtdb preview", '"theme": "dark"' in rtdb.preview(config).body)

        # --- auth pagination
        auth = session.service("auth")
        page1 = auth.table()
        check("auth page 1", len(page1.rows) == 2 and page1.next_token == "2", f"{len(page1.rows)} rows, token {page1.next_token}")
        page2 = auth.table(token=page1.next_token)
        check("auth page 2", len(page2.rows) == 1 and page2.next_token is None, f"{len(page2.rows)} rows")
        found = auth.table(query="grace@example.com")
        check("auth email lookup", len(found.rows) == 1 and found.rows[0][0] == "grace@example.com")

        # --- storage denial is a typed error
        storage = session.service("storage")
        try:
            storage.table("")
            raise AssertionError("storage table should have raised")
        except Exception as exc:  # noqa: BLE001
            check("storage denied raises permission", getattr(exc, "kind", "") == "permission", str(exc)[:60])

        # --- rules preview
        rules = session.service("rules")
        rel = rules.table().items[0]
        body = rules.preview(rel).body
        check("rules source readable", "rules_version" in body and "allow read" in body)

        # --- backup with storage skipped
        with tempfile.TemporaryDirectory() as tmp:
            plan = BackupPlan(dest=Path(tmp), services=list(session.services))
            report = run_backup(session, plan)
            root = report.root
            check("backup root created", root.is_dir(), str(root))
            check("manifest exists", (root / "manifest.json").is_file())
            manifest = json.loads((root / "manifest.json").read_text())
            states = {s["key"]: s for s in manifest["services"]}
            check("storage skipped in manifest", states["storage"]["state"] == "denied", states["storage"].get("error", ""))
            check("manifest totals", manifest["totals"]["services_ok"] >= 6, json.dumps(manifest["totals"]))
            for rel_path in [
                "project/project.json",
                "project/apps.json",
                "firestore/posts/p1.json",
                "firestore/users/u1.json",
                "firestore/users/u1/orders/o1.json",
                "rtdb/database.json",
                "auth/users.ndjson",
                "auth/users.json",
                "remoteconfig/remote_config.json",
                "hosting/sites.json",
                "rules/files/rset-abc/firestore.rules",
                "README.txt",
            ]:
                check(f"backup has {rel_path}", (root / rel_path).is_file())
            check("no storage dir when denied", not (root / "storage").exists())
            lines = (root / "auth/users.ndjson").read_text().strip().splitlines()
            check("all users exported", len(lines) == 3, f"{len(lines)} lines")
            arr = json.loads((root / "auth/users.json").read_text())
            check("users.json is a valid array", len(arr) == 3)
            check("rtdb backup round-trips", json.loads((root / "rtdb/database.json").read_text())["config"]["theme"] == "dark")
            check("firestore doc decoded", json.loads((root / "firestore/users/u1.json").read_text())["data"]["name"] == "Ada")

            # --- selective downloads
            out = Path(tmp) / "selective"
            fs.download(users, out / "users")
            check("selective collection export", (out / "users/u2.json").is_file())
            rtdb.download(config, out / "config.json")
            check("selective rtdb node", json.loads((out / "config.json").read_text())["maxItems"] == 10)
            auth.download(page1.items[0], out / "user.json")
            check("selective auth user", json.loads((out / "user.json").read_text())["localId"] == "uid-001")
            rules.download(rel, out / "rules")
            check("selective ruleset", (out / "rules/firestore.rules").is_file() and (out / "rules/ruleset.json").is_file())

        # --- re-enable storage and mirror it
        import requests

        requests.post(f"{mock.base}/__control", json={"deny": []}, timeout=5)
        storage.probe()
        buckets = storage.table("")
        check("bucket listed", buckets.rows[0][0] == "demo-project.appspot.com", str(buckets.rows[:1]))
        objects = storage.table("b:demo-project.appspot.com/")
        labels = [i.label for i in objects.items]
        check("prefix grouped", "photos/" in labels and "readme.txt" in labels, str(labels))
        photos = storage.table("b:demo-project.appspot.com/photos/")
        check("prefix browsing", sorted(i.label for i in photos.items) == ["a.jpg", "b.jpg"], str([i.label for i in photos.items]))
        with tempfile.TemporaryDirectory() as tmp:
            target = storage.download(photos.items[0], Path(tmp) / "a.jpg")
            check("object bytes downloaded", target.read_bytes().startswith(b"\xff\xd8"), str(target))
            storage.snapshot(Path(tmp), BackupPlan(dest=Path(tmp), services=["storage"]))
            mirrored = Path(tmp) / "storage" / "demo-project.appspot.com" / "photos" / "b.jpg"
            check("storage snapshot mirror", mirrored.is_file() and mirrored.read_bytes() == b"\xff\xd8fakejpg2")
            check("storage meta written", (Path(tmp) / "storage" / "_buckets.json").is_file())


def test_bad_credentials():
    from fbcmd.auth import CredentialError

    try:
        Account.from_text('{"type": "service_account"}')
        raise AssertionError("should reject")
    except CredentialError as exc:
        check("missing fields rejected", "Missing required" in str(exc), str(exc)[:60])
    try:
        Account.from_text("not json at all")
        raise AssertionError("should reject")
    except CredentialError as exc:
        check("non-json rejected", "not JSON" in str(exc), str(exc)[:60])


if __name__ == "__main__":
    print("core e2e")
    test_bad_credentials()
    test_probe_and_browse()
    print(f"\nPASS — {CHECK} checks")
