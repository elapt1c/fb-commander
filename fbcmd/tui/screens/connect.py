"""First screen: paste a service-account key, or pick one you saved."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ...auth import Account, CredentialError, load_saved_accounts
from ...config import credentials_dir
from ...session import Session

LOGO = "[bold #ffa000]FB[/][#3d4a5c] / [/][bold #e6edf6]COMMANDER[/]"


class ConnectScreen(Screen):
    BINDINGS = [
        Binding("f5", "connect", "Connect", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    def __init__(self, preset: Account | None = None):
        super().__init__()
        self.preset = preset
        self.saved = load_saved_accounts()
        self.busy = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="connect-scroll"):
            with Vertical(id="connect-wrap"):
                yield Static(LOGO.strip("\n"), id="logo")
                yield Static("terminal control room for Firebase projects", id="tagline")
                with Vertical(id="card"):
                    yield Static("SERVICE-ACCOUNT KEY", classes="field-label")
                    yield TextArea(
                        "",
                        language="json",
                        show_line_numbers=False,
                        tab_behavior="focus",
                        id="creds",
                    )
                    yield Static(
                        "Paste the entire JSON key here (Ctrl+Shift+V / right-click paste), then press F5.",
                        classes="hint",
                    )
                    yield Static("", id="error")
                    with Horizontal(id="actions"):
                        yield Checkbox("remember on this machine (0600)", id="remember")
                        yield Static("", id="spacer")
                        yield Button("Quit", id="quit")
                        yield Button("Connect", id="connect", variant="primary")
                    yield Static("", id="status")
                if self.saved:
                    with Vertical(id="saved-card"):
                        yield Static("SAVED KEYS", classes="field-label")
                        options = OptionList(id="saved")
                        yield options
                yield Static(
                    f"keys live in {credentials_dir()}  ·  nothing is uploaded anywhere",
                    id="footnote",
                )

    def on_mount(self) -> None:
        if self.saved:
            options = self.query_one("#saved", OptionList)
            for index, account in enumerate(self.saved):
                options.add_option(
                    Option(f"{account.project_id}  ·  {account.client_email}", id=f"saved-{index}")
                )
            options.display = True
        self.query_one("#creds", TextArea).focus()
        self.query_one("#connect", Button).variant = "primary"
        if self.preset is not None:
            self._fill(self.preset)
            self.call_after_refresh(self.start, self.preset)

    def _fill(self, account: Account) -> None:
        import json

        info = dict(account.info)
        info["private_key"] = "…redacted…"
        self.query_one("#creds", TextArea).load_text(json.dumps(info, indent=2))

    # -- status --------------------------------------------------------
    def _status(self, message: str, tone: str = "muted") -> None:
        colors = {"muted": "#7d8899", "error": "#ff6b6b", "ok": "#4dd0a7", "busy": "#ffa000"}
        self.query_one("#status", Static).update(f"[{colors.get(tone, '#7d8899')}]{message}[/]")

    def _error(self, message: str) -> None:
        self.query_one("#error", Static).update(f"[#ff6b6b]{message}[/]")
        self.query_one("#status", Static).update("")

    def _busy(self, busy: bool) -> None:
        self.busy = busy
        self.query_one("#connect", Button).disabled = busy
        self.query_one("#creds", TextArea).read_only = busy

    # -- actions -------------------------------------------------------
    def action_connect(self) -> None:
        if self.busy:
            return
        text = self.query_one("#creds", TextArea).text.strip()
        try:
            account = Account.from_text(text)
        except CredentialError as exc:
            self._error(str(exc))
            return
        self.start(account)

    def action_quit(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
        elif event.button.id == "connect":
            self.action_connect()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id and event.option_id.startswith("saved-"):
            account = self.saved[int(event.option_id.split("-", 1)[1])]
            self._fill(account)
            self.start(account)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "remember":
            self._status(
                "the key will be written to disk with 0600 permissions" if event.value else "",
            )

    # -- connection ----------------------------------------------------
    def start(self, account: Account) -> None:
        if self.busy:
            return
        if not os.environ.get("FB_CMD_ACCESS_TOKEN"):
            try:
                account.check_private_key()
            except CredentialError as exc:
                self._error(str(exc))
                return
        remember = self.query_one("#remember", Checkbox).value
        self._busy(True)
        self.query_one("#error", Static).update("")
        self._status(f"minting an access token for {account.project_id}…", "busy")
        self.run_worker(lambda: self._connect(account, remember), thread=True, exclusive=True, group="connect")

    def _connect(self, account: Account, remember: bool) -> None:
        try:
            session = Session.create(account)
            session.probe_all(lambda key, probe: self.app.call_from_thread(self._probe_done, key, probe))
            if remember:
                try:
                    account.save()
                except OSError as exc:
                    self.app.call_from_thread(self._status, f"could not save key: {exc}", "error")
        except Exception as exc:  # noqa: BLE001 - everything becomes a readable error
            self.app.call_from_thread(self._failed, str(exc))
            return
        self.app.call_from_thread(self._connected, session)

    def _probe_done(self, key: str, probe) -> None:
        self._status(f"checked {key} — {probe.state.value}", "busy")

    def _failed(self, message: str) -> None:
        self._busy(False)
        self._status("could not connect", "error")
        self._error(message[:600])

    def _connected(self, session: Session) -> None:
        from .main import MainScreen

        self._busy(False)
        self.app.push_screen(MainScreen(session))
