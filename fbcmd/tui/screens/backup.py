"""Backup options dialog and the live backup runner."""

from __future__ import annotations

import threading
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

from ...backup import BackupPlan, BackupReport, run_backup
from ...config import DEFAULT_BACKUP_ROOT
from ...session import Session


class ServicePicker(OptionList):
    """Compact multi-select: [x] rows, ASCII-safe, denied services unselectable."""

    def __init__(self, session: Session, id: str | None = None):
        super().__init__(id=id)
        self.session = session
        self.choices: dict[str, bool] = {}
        for key, _service in session.services.items():
            probe = session.probes.get(key)
            usable = bool(probe and probe.state.usable)
            self.choices[key] = usable
            self.add_option(Option(self._prompt(key), id=key, disabled=not usable))

    def _prompt(self, key: str) -> Text:
        service = self.session.services[key]
        probe = self.session.probes.get(key)
        on = self.choices.get(key, False)
        text = Text()
        text.append("[x] " if on else "[ ] ", style=("#4dd0a7" if on else "#3d4a5c"))
        text.append(f"{service.icon} ", style=service.accent)
        text.append(service.label, style=("#e6edf6" if on else "#5d6b7e"))
        if not (probe and probe.state.usable):
            text.append(f"   ({probe.state.value if probe else 'unknown'})", style="#7d8899")
        return text

    def toggle(self, key: str) -> None:
        if key not in self.choices:
            return
        option = self.get_option(key)
        if option.disabled:
            return
        index = self.get_option_index(key)
        self.choices[key] = not self.choices[key]
        self.clear_options()
        for service_key in self.session.services:
            self.add_option(
                Option(
                    self._prompt(service_key),
                    id=service_key,
                    disabled=not (self.session.probes.get(service_key) and self.session.probes[service_key].state.usable),
                )
            )
        if index >= 0:
            self.highlighted = index

    def chosen(self) -> list[str]:
        return [key for key in self.session.services if self.choices.get(key)]


class BackupDialog(ModalScreen[BackupPlan | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    BackupDialog { align: center middle; }
    BackupDialog #dialog { width: 84; max-width: 96%; height: auto; max-height: 90%;
                           background: #111722; border: round #ffa000; padding: 1 2; }
    BackupDialog #checks { height: auto; max-height: 12; overflow-y: auto; }
    BackupDialog ServicePicker { background: transparent; border: none; height: auto; padding: 0; }
    BackupDialog ServicePicker > .option-list--option { padding: 0 1; }
    BackupDialog ServicePicker:focus { border: none; }
    BackupDialog .row { height: 3; }
    BackupDialog .row Static { width: 22; content-align: left middle; color: #8fa1b7; }
    BackupDialog .row Input { width: 1fr; }
    BackupDialog #dialog-buttons { height: 3; align-horizontal: right; }
    BackupDialog #dialog-buttons Button { margin-left: 2; }
    """

    def __init__(self, session: Session):
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[bold #ffa000]NEW BACKUP[/]  [#7d8899]everything this key can read[/]", classes="dialog-title")
            yield Static(f"[#8fa1b7]{self.session.project_id}[/]  →  folders of plain JSON + mirrored files", classes="hint")
            with Vertical(id="checks"):
                yield ServicePicker(self.session, id="services")
            with Vertical(id="rows"):
                with Horizontal(classes="row"):
                    yield Static("destination")
                    yield Input(str(DEFAULT_BACKUP_ROOT), id="dest")
                with Horizontal(classes="row"):
                    yield Static("rtdb subtree")
                    yield Input("/", id="rtdb-path", placeholder="/ or /users")
                with Horizontal(classes="row"):
                    yield Static("storage prefix")
                    yield Input("", id="storage-prefix", placeholder="(all objects)")
                with Horizontal(classes="row"):
                    yield Static("max file size (MB)")
                    yield Input("0", id="max-mb", placeholder="0 = no limit")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Start backup", id="start", variant="primary")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.query_one("#services", ServicePicker).toggle(event.option_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id != "start":
            return
        services = self.query_one("#services", ServicePicker).chosen()
        if not services:
            self.query_one(".dialog-title", Static).update("[bold #ff6b6b]Pick at least one service[/]")
            return
        try:
            max_mb = int(self.query_one("#max-mb", Input).value or 0)
        except ValueError:
            max_mb = 0
        plan = BackupPlan(
            dest=Path(self.query_one("#dest", Input).value or str(DEFAULT_BACKUP_ROOT)).expanduser(),
            services=services,
            rtdb_path=self.query_one("#rtdb-path", Input).value or "/",
            storage_prefix=self.query_one("#storage-prefix", Input).value.strip(),
            max_file_bytes=max(0, max_mb) * 1024 * 1024,
        )
        self.dismiss(plan)

    def action_cancel(self) -> None:
        self.dismiss(None)


class BackupRunner(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]
    DEFAULT_CSS = """
    BackupRunner { align: center middle; }
    BackupRunner #runner { width: 96; max-width: 96%; height: auto; max-height: 90%;
                           background: #111722; border: round #4dd0a7; padding: 1 2; }
    BackupRunner #current { height: auto; min-height: 2; color: #c9d4e3; }
    BackupRunner #log { height: 14; background: #0a0e15; border: round #1d2635; }
    BackupRunner #bar { height: 1; margin: 1 0; }
    BackupRunner #summary { height: auto; }
    BackupRunner #runner-buttons { height: 3; align-horizontal: right; }
    BackupRunner #runner-buttons Button { margin-left: 2; }
    """

    def __init__(self, session: Session, plan: BackupPlan):
        super().__init__()
        self.session = session
        self.plan = plan
        self.cancel_event = threading.Event()
        self.plan.cancel = self.cancel_event
        self.report: BackupReport | None = None
        self._started: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="runner"):
            yield Static("[bold #4dd0a7]BACKUP RUNNING[/]", id="runner-title", classes="dialog-title")
            yield Static("", id="current")
            yield ProgressBar(total=max(1, len(self.plan.services)), show_eta=False, id="bar")
            yield RichLog(id="log", markup=True, wrap=True, max_lines=400)
            yield Static("", id="summary")
            with Horizontal(id="runner-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Close", id="close", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#log", RichLog).write(
            f"[#7d8899]writing to[/] {self.plan.dest}\n[#7d8899]services:[/] {', '.join(self.plan.services)}"
        )
        self.run_worker(self._run, thread=True, exclusive=True, group="backup")

    def _run(self) -> None:
        try:
            report = run_backup(self.session, self.plan, self._progress)
        except Exception as exc:  # noqa: BLE001 - report instead of killing the modal
            self.app.call_from_thread(self._failed, str(exc))
            return
        self.app.call_from_thread(self._finished, report)

    def _progress(self, key: str, message: str, done=None, total=None) -> None:
        if key == "__done__":
            return
        self.app.call_from_thread(self._tick, key, message)

    def _tick(self, key: str, message: str) -> None:
        label = self.session.services[key].label if key in self.session.services else key
        if key not in self._started:
            self._started.add(key)
            self.query_one("#bar", ProgressBar).advance(1)
            self.query_one("#log", RichLog).write(f"[bold #ffa000]{label}[/] [#7d8899]{message}[/]")
        short = message if len(message) < 90 else message[:87] + "…"
        self.query_one("#current", Static).update(f"[#7d8899]{label}[/]  [#c9d4e3]{short}[/]")

    def _finished(self, report: BackupReport) -> None:
        self.report = report
        self.query_one("#runner-title", Static).update("[bold #4dd0a7]BACKUP COMPLETE[/]")
        summary = Text()
        summary.append("DONE  ", style="bold #4dd0a7")
        summary.append(f"{report.total_files} files · {report.total_bytes:,} bytes\n", style="#c9d4e3")
        summary.append(str(report.root), style="bold #79c0ff")
        for result in report.results:
            summary.append("\n")
            color = "#4dd0a7" if result.state == "ok" else ("#e3b341" if result.state in ("empty", "denied", "disabled") else "#ff6b6b")
            summary.append(f"  {result.service:<13}", style="#8fa1b7")
            summary.append(f"{result.state:<9}", style=color)
            if result.error:
                summary.append(result.error[:110], style="#7d8899")
            elif result.files:
                summary.append(f"{result.files} files", style="#7d8899")
        self.query_one("#summary", Static).update(summary)
        self.query_one("#current", Static).update("")
        self.query_one("#cancel", Button).disabled = True
        close = self.query_one("#close", Button)
        close.disabled = False
        close.focus()
        self.query_one("#log", RichLog).write(f"[bold #4dd0a7]manifest[/] {report.manifest}")
        self.app.notify(f"Backup written to {report.root}", title="Backup complete", timeout=10)

    def _failed(self, message: str) -> None:
        self.query_one("#runner-title", Static).update("[bold #ff6b6b]BACKUP FAILED[/]")
        self.query_one("#summary", Static).update(f"[#ff6b6b]backup failed: {message[:400]}[/]")
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#close", Button).disabled = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.query_one("#services", ServicePicker).toggle(event.option_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.cancel_event.set()
            event.button.disabled = True
            self.query_one("#current", Static).update(
                "[#e3b341]cancelling — the current service finishes first[/]"
            )
        elif event.button.id == "close":
            self.dismiss(None)

    def action_close(self) -> None:
        if self.report is not None or self.cancel_event.is_set():
            self.dismiss(None)
