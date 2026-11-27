"""The Textual application object."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from ..auth import Account
from ..config import DEFAULT_BACKUP_ROOT
from .screens.connect import ConnectScreen


class FbCommander(App):
    CSS_PATH = "app.tcss"
    TITLE = "fb-commander"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", show=False)]

    def __init__(self, account: Account | None = None, backup_root: Path | None = None):
        super().__init__()
        self.account = account
        self.backup_root = Path(backup_root or DEFAULT_BACKUP_ROOT).expanduser()

    @property
    def download_root(self) -> Path:
        return self.backup_root / "downloads"

    def on_mount(self) -> None:
        self.push_screen(ConnectScreen(self.account))

    def open_service(self, key: str) -> None:
        from .screens.main import MainScreen

        if isinstance(self.screen, MainScreen):
            self.screen.show_service(key)
