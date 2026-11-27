"""Drive the real TUI against the fixture server and capture screenshots.

    .venv/bin/python tests/e2e_tui.py [outdir]

Screenshots are SVG (Textual's own renderer); the script also asserts the
navigation, previews, permission greying and download/backup flows worked.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fbcmd.auth import Account  # noqa: E402
from fbcmd.tui.app import FbCommander  # noqa: E402
from tests.mock_firebase import PROJECT, MockFirebase  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def account() -> Account:
    info = {
        "type": "service_account",
        "project_id": PROJECT,
        "private_key_id": "0f1e2d3c4b5a",
        "private_key": "-----BEGIN PRIVATE KEY-----\nRkFLRQ==\n-----END PRIVATE KEY-----\n",
        "client_email": f"firebase-adminsdk@ {PROJECT}.iam.gserviceaccount.com".replace(" ", ""),
    }
    return Account.from_text(json.dumps(info))


async def wait_for(pilot, predicate, timeout=20.0):
    waited = 0.0
    while waited < timeout:
        await pilot.pause(0.2)
        waited += 0.2
        if predicate():
            return True
    return False


async def shoot(pilot, app, name: str) -> None:
    await pilot.pause(0.3)
    path = OUT / f"{name}.svg"
    app.save_screenshot(str(path))
    print(f"  shot {path.name} ({path.stat().st_size} bytes)")


async def run() -> None:
    from textual.widgets import DataTable, Static, TextArea, Tree

    with MockFirebase(deny={"storage"}) as mock:
        os.environ.update(mock.env())
        with tempfile.TemporaryDirectory() as tmp:
            app = FbCommander(account=None, backup_root=Path(tmp))
            async with app.run_test(size=(152, 46)) as pilot:
                await pilot.pause()
                assert app.screen.__class__.__name__ == "ConnectScreen", app.screen
                await shoot(pilot, app, "01-connect")

                # a truncated key must be refused locally, before any network call
                bad = dict(account().info)
                bad["private_key"] = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
                saved_token = os.environ.pop("FB_CMD_ACCESS_TOKEN", None)
                app.screen.query_one("#creds", TextArea).load_text(json.dumps(bad))
                await pilot.press("f5")
                await pilot.pause(0.3)
                assert "not a valid RSA key" in str(app.screen.query_one("#error", Static).render())
                assert app.screen.__class__.__name__ == "ConnectScreen"
                if saved_token is not None:
                    os.environ["FB_CMD_ACCESS_TOKEN"] = saved_token

                app.screen.query_one("#creds", TextArea).load_text(json.dumps(account().info))
                await pilot.press("f5")
                ok = await wait_for(pilot, lambda: app.screen.__class__.__name__ == "MainScreen", 40)
                assert ok, f"never reached main screen: {app.screen}"

                main = app.screen
                assert main.session.project_id == PROJECT
                assert main.session.probes["storage"].state.value == "denied"
                await shoot(pilot, app, "02-dashboard")

                # --- Firestore tree + preview
                main.show_service("firestore")
                ok = await wait_for(pilot, lambda: bool(main.query_one(Tree, Tree).root.children), 15)
                assert ok, "firestore roots never loaded"
                tree = main.query_one(Tree, Tree)
                labels = [node.label.plain for node in tree.root.children]
                assert labels == ["posts", "users"], labels
                users = tree.root.children[1]
                users.expand()
                await wait_for(pilot, lambda: len(users.children) == 2 or users.children[0].label.plain != "loading…", 10)
                await pilot.pause(0.5)
                assert [c.label.plain for c in users.children] == ["u1", "u2"], [c.label.plain for c in users.children]
                tree.select_node(users.children[0])
                await wait_for(
                    pilot,
                    lambda: "Ada" in main.query_one("#preview-body", TextArea).text,
                    10,
                )
                await shoot(pilot, app, "03-firestore-tree")

                # --- Auth table + preview
                main.show_service("auth")
                ok = await wait_for(pilot, lambda: main.query_one(DataTable, DataTable).row_count >= 2, 15)
                assert ok, "auth rows never loaded"
                table = main.query_one(DataTable, DataTable)
                assert table.row_count == 2, table.row_count
                table.move_cursor(row=0)
                await wait_for(pilot, lambda: "ada@example.com" in main.query_one("#preview-body", TextArea).text, 10)
                await shoot(pilot, app, "04-auth")

                # --- Storage locked
                main.show_service("storage")
                await pilot.pause(0.6)
                head = str(main.query_one("#pane-head", Static).render())
                assert "no access" in head, head
                assert "Permission denied" in str(main.query_one(".lock-body", Static).render())
                await shoot(pilot, app, "05-storage-denied")

                # --- Backup dialog
                main.show_service("dashboard")
                await pilot.press("b")
                ok = await wait_for(pilot, lambda: app.screen.__class__.__name__ == "BackupDialog", 10)
                assert ok
                await shoot(pilot, app, "06-backup-dialog")
                await pilot.press("escape")
                await wait_for(pilot, lambda: app.screen is main, 5)

                # --- Help
                await pilot.press("?")
                await wait_for(pilot, lambda: app.screen.__class__.__name__ == "HelpScreen", 5)
                await shoot(pilot, app, "07-help")
                await pilot.press("escape")
                await wait_for(pilot, lambda: app.screen is main, 5)

                # --- Rules preview (text, not JSON)
                main.show_service("rules")
                await wait_for(pilot, lambda: main.query_one(DataTable, DataTable).row_count >= 1, 15)
                main.query_one(DataTable, DataTable).move_cursor(row=0)
                await wait_for(pilot, lambda: "rules_version" in main.query_one("#preview-body", TextArea).text, 10)
                await shoot(pilot, app, "08-rules")

                # --- Real backup through the runner
                main.show_service("dashboard")
                from fbcmd.backup import BackupPlan
                from fbcmd.tui.screens.backup import BackupRunner

                plan = BackupPlan(dest=Path(tmp) / "backups", services=list(main.session.services))
                runner = BackupRunner(main.session, plan)
                app.push_screen(runner)
                ok = await wait_for(pilot, lambda: runner.report is not None, 40)
                assert ok, "backup runner never finished"
                assert runner.report.total_files > 10, runner.report.total_files
                assert (runner.report.root / "manifest.json").is_file()
                await shoot(pilot, app, "09-backup-done")
                await pilot.press("escape")
                await wait_for(pilot, lambda: app.screen is main, 5)

                print("PASS — TUI flows verified")


if __name__ == "__main__":
    asyncio.run(run())
