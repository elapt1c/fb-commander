"""Preview pane: title, metadata chips and a scrollable read-only text view."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, TextArea

from ...models import Preview


class PreviewPane(Vertical):
    DEFAULT_CSS = """
    PreviewPane { height: 1fr; }
    PreviewPane #preview-body { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Static("PREVIEW", id="preview-title", classes="pane-title")
        yield Static("select something to inspect it", id="preview-meta", classes="preview-meta")
        yield TextArea(
            "",
            language="json",
            read_only=True,
            show_line_numbers=True,
            soft_wrap=True,
            id="preview-body",
        )

    def _body(self) -> TextArea:
        return self.query_one("#preview-body", TextArea)

    def show(self, preview: Preview) -> None:
        self.query_one("#preview-title", Static).update(preview.title)
        meta = "   ".join(f"[#7d8899]{key}[/] [#c9d4e3]{value}[/]" for key, value in preview.meta[:5])
        self.query_one("#preview-meta", Static).update(meta or "[#7d8899]—[/]")
        body = self._body()
        body.language = preview.language if preview.language in ("json", "text", "python", "javascript") else "json"
        body.load_text(preview.body)

    def message(self, text: str, tone: str = "muted") -> None:
        colors = {"muted": "#7d8899", "error": "#ff6b6b", "ok": "#4dd0a7"}
        self.query_one("#preview-title", Static).update("PREVIEW")
        self.query_one("#preview-meta", Static).update(f"[{colors.get(tone, '#7d8899')}]{text}[/]")
        self._body().load_text("")

    def clear(self) -> None:
        self.message("select something to inspect it")
