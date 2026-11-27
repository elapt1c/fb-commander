"""Table browser for flat services (Auth, Storage, Remote Config, Hosting, Rules)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Static

from ...models import Item, TablePage
from ...services.base import Service
from .tree_browser import ItemHighlighted, LoadFailed


class TableBrowser(Vertical):
    DEFAULT_CSS = """
    TableBrowser { height: 1fr; }
    TableBrowser #table { height: 1fr; }
    TableBrowser #crumbs { height: auto; }
    """

    def __init__(self, service: Service):
        super().__init__()
        self.service = service
        self.page: TablePage | None = None
        self.items: list[Item] = []
        self.selected: Item | None = None
        self.path = ""
        self.query_text = ""
        self._offset = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="crumbs"):
            yield Static("", id="crumb-text")
            yield Button("more", id="more", classes="mini")
        if self.service.key == "auth":
            yield Input(placeholder="filter by email or uid  ⏎ (server-side lookup)", id="search")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns(*(["…"]))
        table.focus()
        self.load(self.path)

    # -- loading -------------------------------------------------------
    def load(self, path: str | None = None, token: str | None = None, query: str | None = None, append: bool = False) -> None:
        if path is not None:
            self.path = path
        if query is not None:
            self.query_text = query
        self.run_worker(
            lambda: self._fetch(self.path, token, self.query_text),
            thread=True,
            exclusive=True,
            group="table",
        )

    def _fetch(self, path: str, token: str | None, query: str) -> None:
        try:
            page = self.service.table(path, token=token, query=query)
        except Exception as exc:  # noqa: BLE001 - shown in the UI
            self.app.call_from_thread(self.post_message, LoadFailed(str(exc)))
            return
        self.app.call_from_thread(self._apply, page, token is not None)

    def _apply(self, page: TablePage, append: bool) -> None:
        table = self.query_one("#table", DataTable)
        if not append or self.page is None or not self._same_view(page):
            table.clear(columns=True)
            table.add_columns(*page.columns)
            self.items = []
            self._offset = 0
        self.page = page
        for row, item in zip(page.rows, page.items or [None] * len(page.rows)):
            table.add_row(*row, key=f"r{self._offset}")
            self.items.append(item)  # type: ignore[arg-type]
            self._offset += 1
        if len(page.items or []) < len(page.rows):
            self.items.extend([None] * (len(page.rows) - len(page.items or [])))  # type: ignore[arg-type]
        self._render_crumbs(page)
        self.query_one("#more", Button).display = bool(page.next_token)
        table.focus()
        if table.row_count:
            table.move_cursor(row=0)
        self._describe(page)

    def _same_view(self, page: TablePage) -> bool:
        return self.page is not None and [c for c in page.breadcrumbs] == [c for c in self.page.breadcrumbs]

    def _render_crumbs(self, page: TablePage) -> None:
        label = "  ›  ".join(name for name, _ in page.breadcrumbs) or page.title
        self.query_one("#crumb-text", Static).update(f"[#7d8899]{label}[/]")

    def _describe(self, page: TablePage) -> None:
        bits = [page.subtitle] if page.subtitle else []
        if page.next_token:
            bits.append("more available — press n")
        if page.note:
            bits.append(page.note)
        self.post_message(TableLoaded(" · ".join(bits)))

    # -- events --------------------------------------------------------
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        item = self._item_for(event.row_key.value)
        self.selected = item
        self.post_message(ItemHighlighted(item))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        item = self._item_for(event.row_key.value)
        if item is None:
            return
        self.selected = item
        target = self.open(item)
        if target is not None:
            self.load(target)

    def _item_for(self, key: str | None) -> Item | None:
        if key is None:
            return None
        try:
            index = int(str(key).lstrip("r"))
        except ValueError:
            return None
        if 0 <= index < len(self.items):
            return self.items[index]
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "more" and self.page and self.page.next_token:
            self.load(token=self.page.next_token, append=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.load(token=None, query=event.value.strip())

    # -- navigation ----------------------------------------------------
    def open(self, item: Item) -> str | None:
        if self.service.key == "storage":
            if item.kind == "bucket":
                return f"b:{item.label}"
            if item.kind == "prefix":
                import json

                _, bucket, prefix = json.loads(item.key)
                return f"b:{bucket}/{prefix}"
        if self.service.key == "hosting" and item.kind == "site":
            return f"s:{item.label}"
        return None

    def go_up(self) -> bool:
        if self.page and len(self.page.breadcrumbs) > 1:
            self.load(self.page.breadcrumbs[-2][1])
            return True
        return False

    def load_more(self) -> bool:
        if self.page and self.page.next_token:
            self.load(token=self.page.next_token, append=True)
            return True
        return False

    def focus_search(self) -> None:
        try:
            self.query_one("#search", Input).focus()
        except Exception:  # noqa: BLE001 - only Auth has a search box
            pass


class TableLoaded(Message):
    def __init__(self, summary: str) -> None:
        super().__init__()
        self.summary = summary
