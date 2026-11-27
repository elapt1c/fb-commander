"""Keyboard reference."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

NAV = [
    ("↑ ↓", "move in the service rail, tree or table"),
    ("→ ←", "expand / collapse a node"),
    ("Enter", "open a bucket, prefix, site or row"),
    ("Backspace", "up one level"),
    ("/", "search users by email or uid"),
    ("n", "load the next page"),
    ("Esc", "put focus back on the service rail"),
    ("Ctrl+N", "connect another project"),
]

DATA = [
    ("d", "download the highlighted item"),
    ("b", "backup dialog (services, subtree, prefix, destination)"),
    ("r", "re-probe access and reload the service"),
    ("?", "this help"),
    ("q", "quit"),
]


def _section(title: str, rows: list[tuple[str, str]]) -> Text:
    text = Text()
    text.append(title.upper(), style="bold #5d6b7e")
    text.append("\n")
    for key, description in rows:
        text.append("  ")
        text.append(f"{key:<11}", style="bold #ffa000")
        text.append(description, style="#c9d4e3")
        text.append("\n")
    text.append("\n")
    return text


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("question_mark", "close", "Close")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen #help-dialog {
        width: 92; max-width: 96%; height: 34; max-height: 96%;
        background: #10161f; border: round #ffa000; padding: 1 2;
    }
    HelpScreen #help-scroll { height: 1fr; background: transparent; }
    HelpScreen #help-body { height: auto; background: transparent; }
    HelpScreen #help-foot { height: 3; align-horizontal: right; }
    """

    def compose(self) -> ComposeResult:
        body = Text()
        body.append("FB", style="bold #ffa000")
        body.append(" / COMMANDER", style="bold #e6edf6")
        body.append("      terminal control room for Firebase projects\n\n", style="#5d6b7e")
        body.append_text(_section("navigation", NAV))
        body.append_text(_section("data", DATA))

        body.append("WHERE BACKUPS GO", style="bold #5d6b7e")
        body.append("\n")
        body.append("  ~/firebase-backups/<project>-<timestamp>/\n", style="#c9d4e3")
        body.append(
            "  manifest.json        what ran, what was skipped and why\n"
            "  firestore/           one JSON file per document, subcollections nested\n"
            "  rtdb/                the whole database (or the subtree you picked)\n"
            "  auth/users.ndjson    one user per line, plus users.json\n"
            "  storage/<bucket>/    byte-for-byte mirror of every readable object\n"
            "  remoteconfig/  hosting/  rules/  project/\n",
            style="#7d8899",
        )
        body.append("\nServices you cannot read stay visible but dimmed; a backup records the denial "
                    "instead of failing.", style="#5d6b7e")
        with Vertical(id="help-dialog"):
            with VerticalScroll(id="help-scroll"):
                yield Static(body, id="help-body")
            with Horizontal(id="help-foot"):
                yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
