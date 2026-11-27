"""Entry points: the TUI (default) plus headless probe/backup commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .auth import Account, CredentialError, load_saved_accounts
from .backup import BackupPlan, run_backup
from .config import DEFAULT_BACKUP_ROOT, Endpoints
from .session import Session


def resolve_account(creds: str | None) -> Account:
    if creds:
        text = Path(creds).expanduser().read_text(encoding="utf-8") if Path(creds).expanduser().is_file() else creds
        return Account.from_text(text)
    inline = os.environ.get("FB_CMD_CREDS_JSON")
    if inline:
        return Account.from_text(inline)
    saved = load_saved_accounts()
    if len(saved) == 1:
        return saved[0]
    if not saved:
        raise CredentialError("No credentials: pass --creds PATH or run `fb-commander` and paste a key.")
    raise CredentialError("Several saved projects — pass --creds PATH to choose one.")


def _connect(account: Account) -> Session:
    if not os.environ.get("FB_CMD_ACCESS_TOKEN"):
        account.check_private_key()
    session = Session.create(account, Endpoints())
    session.probe_all()
    return session


def cmd_probe(args) -> int:
    from rich.console import Console
    from rich.table import Table

    session = _connect(resolve_account(args.creds))
    table = Table(title=f"{session.project_id} · {session.account.client_email}", show_lines=False)
    table.add_column("SERVICE")
    table.add_column("STATE")
    table.add_column("DETAIL", overflow="fold")
    colors = {"ok": "green", "empty": "yellow", "denied": "red", "disabled": "yellow", "error": "red", "checking": "dim"}
    for key, probe in session.probes.items():
        service = session.services[key]
        table.add_row(f"{service.icon} {service.label}", f"[{colors.get(probe.state.value, 'white')}]{probe.state.value}[/]", probe.detail)
    Console().print(table)
    return 0


def cmd_backup(args) -> int:
    from rich.console import Console

    console = Console(stderr=True)
    session = _connect(resolve_account(args.creds))
    keys = list(session.services) if args.services in (None, "all") else [k.strip() for k in args.services.split(",") if k.strip()]
    unknown = [k for k in keys if k not in session.services]
    if unknown:
        console.print(f"[red]unknown service(s): {', '.join(unknown)}[/]")
        return 2
    plan = BackupPlan(
        dest=Path(args.out or DEFAULT_BACKUP_ROOT).expanduser(),
        services=keys,
        rtdb_path=args.rtdb_path or "/",
        storage_prefix=args.storage_prefix or "",
        max_file_bytes=int(args.max_file_mb) * 1024 * 1024,
        label=args.label or "",
    )

    def progress(key: str, message: str, done=None, total=None):
        if key == "__done__":
            return
        suffix = f" [{done}/{total}]" if done is not None and total else ""
        console.print(f"  {key:<12} {message}{suffix}")

    console.print(f"[bold]backing up[/] {session.project_id} → {plan.dest}")
    report = run_backup(session, plan, progress)
    print(json.dumps({"root": str(report.root), "files": report.total_files, "bytes": report.total_bytes,
                      "failed": [r.service for r in report.failed],
                      "skipped": [r.service for r in report.skipped]}, indent=2))
    console.print(f"[green]done[/] {report.root} · {report.total_files} files · {report.total_bytes} bytes")
    return 0 if not report.failed else 1


def cmd_tui(args) -> int:
    from .tui.app import FbCommander

    account = None
    if args.creds:
        try:
            account = resolve_account(args.creds)
        except CredentialError as exc:
            print(f"fb-commander: {exc}", file=sys.stderr)
            return 2
    FbCommander(account=account).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fb-commander", description="Terminal control room for Firebase projects.")
    sub = parser.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="open the interactive UI (default)")
    tui.add_argument("--creds", help="service-account JSON path (or raw JSON)")
    tui.set_defaults(func=cmd_tui)

    probe = sub.add_parser("probe", help="print what the key can access and exit")
    probe.add_argument("--creds")
    probe.set_defaults(func=cmd_probe)

    backup = sub.add_parser("backup", help="headless backup of every accessible service")
    backup.add_argument("--creds")
    backup.add_argument("-o", "--out", help=f"output parent dir (default {DEFAULT_BACKUP_ROOT})")
    backup.add_argument("--services", default="all", help="all or comma-separated: project,firestore,rtdb,auth,storage,remoteconfig,hosting,rules")
    backup.add_argument("--rtdb-path", default="/", help="only export this Realtime Database subtree")
    backup.add_argument("--storage-prefix", default="", help="only mirror objects under this prefix")
    backup.add_argument("--max-file-mb", default=0, help="skip storage objects bigger than this (0 = no limit)")
    backup.add_argument("--label", default="", help="free-form note stored in manifest.json")
    backup.set_defaults(func=cmd_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        argv = ["tui"]
    elif argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["tui", *argv]
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CredentialError as exc:
        print(f"fb-commander: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
