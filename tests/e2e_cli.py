"""Headless CLI end-to-end: probe + backup + credential validation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.mock_firebase import PROJECT, MockFirebase  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
CHECK = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global CHECK
    CHECK += 1
    if not condition:
        raise AssertionError(f"{label} failed: {extra}")
    print(f"  ok  {label}" + (f" — {extra}" if extra else ""))


def creds_file(path: Path, key: str) -> None:
    info = {
        "type": "service_account",
        "project_id": PROJECT,
        "private_key_id": "fake-key-id",
        "private_key": key,
        "client_email": f"firebase-adminsdk@{PROJECT}.iam.gserviceaccount.com",
    }
    path.write_text(json.dumps(info))


def main() -> None:
    with MockFirebase(deny={"storage"}) as mock:
        env = {**os.environ, **mock.env(), "PYTHONPATH": str(ROOT)}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            creds = tmp_path / "sa.json"
            creds_file(creds, "-----BEGIN PRIVATE KEY-----\nRkFLRQ==\n-----END PRIVATE KEY-----\n")

            probe = subprocess.run(
                [PY, "-m", "fbcmd", "probe", "--creds", str(creds)],
                capture_output=True, text=True, env=env, cwd=ROOT,
            )
            check("probe exits 0", probe.returncode == 0, probe.stderr[-300:])
            check("probe lists storage denied", "Cloud Storage" in probe.stdout and "denied" in probe.stdout)
            check("probe lists rules", "Security Rules" in probe.stdout)

            backup = subprocess.run(
                [PY, "-m", "fbcmd", "backup", "--creds", str(creds), "-o", str(tmp_path / "out")],
                capture_output=True, text=True, env=env, cwd=ROOT,
            )
            check("backup exits 0", backup.returncode == 0, backup.stderr[-500:])
            payload = json.loads(backup.stdout[backup.stdout.index("{"):])
            check("backup reports files", payload["files"] > 10, str(payload))
            root = Path(payload["root"])
            check("backup folder exists", root.is_dir(), str(root))
            check("backup manifest written", (root / "manifest.json").is_file())
            manifest = json.loads((root / "manifest.json").read_text())
            check("manifest records the denial", any(s["key"] == "storage" and s["state"] == "denied" for s in manifest["services"]))
            check("firestore doc on disk", (root / "firestore" / "users" / "u2.json").is_file())

            selective = subprocess.run(
                [PY, "-m", "fbcmd", "backup", "--creds", str(creds), "-o", str(tmp_path / "sub"),
                 "--services", "rtdb", "--rtdb-path", "/users"],
                capture_output=True, text=True, env=env, cwd=ROOT,
            )
            check("selective backup exits 0", selective.returncode == 0, selective.stderr[-300:])
            sub_root = Path(json.loads(selective.stdout[selective.stdout.index("{"):])["root"])
            check("only rtdb selected", not (sub_root / "firestore").exists() and (sub_root / "rtdb" / "subtree.json").is_file())
            subtree = json.loads((sub_root / "rtdb" / "subtree.json").read_text())
            check("subtree content", "u1" in subtree and "u2" in subtree, str(list(subtree)))

            bad = tmp_path / "bad.json"
            creds_file(bad, "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
            broken = subprocess.run(
                [PY, "-m", "fbcmd", "probe", "--creds", str(bad)],
                capture_output=True, text=True, env={k: v for k, v in env.items() if k != "FB_CMD_ACCESS_TOKEN"}, cwd=ROOT,
            )
            check("corrupted key refused", broken.returncode == 2, str(broken.returncode))
            check("corrupted key message is clear", "not a valid RSA key" in broken.stderr, broken.stderr.strip()[-200:])
    print(f"\nPASS — {CHECK} CLI checks")


if __name__ == "__main__":
    main()
