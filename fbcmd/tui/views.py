"""Dashboards and per-service browsing views."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Static

from ..models import Item, Probe, ServiceState
from ..services import Service
from ..session import Session
from .util import STATE_COLOR, STATE_WORD, download_relpath
from .widgets.preview import PreviewPane
from .widgets.table_browser import TableBrowser
from .widgets.tree_browser import ItemHighlighted, LoadFailed, TreeBrowser


class ServiceCard(Static):
    def __init__(self, service: Service, probe: Probe):
        self.service_key = service.key
        self.service = service
        self.probe = probe
        super().__init__(self._content(), classes=f"card state-{probe.state.value}")

    def _content(self) -> Text:
        text = Text()
        color = STATE_COLOR.get(self.probe.state, "#7d8899")
        text.append(f"{self.service.icon}  ", style=f"bold {self.service.accent}")
        text.append(self.service.label, style="bold #e6edf6")
        text.append(f"   {STATE_WORD.get(self.probe.state, '')}", style=color)
        text.append("\n")
        text.append(self.service.blurb, style="#7d8899")
        text.append("\n")
        text.append(self.probe.detail or "—", style="#a9b6c7")
        return text

    def on_click(self) -> None:
        self.app.open_service(self.service_key)


class DashboardView(ScrollableContainer):
    def __init__(self, session: Session):
        super().__init__(classes="dashboard")
        self.session = session

    def compose(self) -> ComposeResult:
        session = self.session
        probes = session.probes
        ready = [k for k, p in probes.items() if p.state.usable]
        denied = [(k, p) for k, p in probes.items() if p.state is ServiceState.DENIED]

        hero = Text()
        hero.append("FIREBASE CONTROL ROOM", style="bold #ffa000")
        hero.append("\n")
        hero.append(session.project_id, style="bold #e6edf6")
        hero.append("   ·   ", style="#3d4a5c")
        hero.append(session.account.client_email, style="#8fa1b7")
        status = Text()
        status.append("● CONNECTED", style="bold #4dd0a7")
        status.append("\n")
        status.append(f"key {session.account.private_key_id[:12] or '—'}", style="#5d6b7e")
        with Horizontal(id="hero"):
            yield Static(hero, id="hero-left")
            yield Static(status, id="hero-right")

        metrics = Text()
        metrics.append(f"{len(ready)}", style="bold #4dd0a7")
        metrics.append(" services reachable", style="#8fa1b7")
        metrics.append("      ")
        metrics.append(f"{len(denied)}", style=("bold #ff6b6b" if denied else "bold #4dd0a7"))
        metrics.append(" locked", style="#8fa1b7")
        metrics.append("      ")
        metrics.append(f"{len(session.services) - len(ready) - len(denied)}", style="bold #e3b341")
        metrics.append(" unavailable", style="#8fa1b7")
        metrics.append("      ")
        metrics.append("read-only · v1.0", style="#5d6b7e")
        yield Static(metrics, classes="metrics")

        for key, probe in denied:
            service = session.services[key]
            line = Text()
            line.append(f"{service.icon} {service.label}", style="bold #ff6b6b")
            line.append("   ", style="")
            line.append(probe.detail or "the service account lacks a role for this API", style="#a9b6c7")
            yield Static(line, classes="warning")

        cards = Text()
        cards.append("SERVICES", style="bold #5d6b7e")
        yield Static(cards, classes="section-title")
        with Horizontal(classes="cards"):
            for key, service in session.services.items():
                yield ServiceCard(service, probes.get(key, Probe()))

        yield Static(
            "[#5d6b7e]↑↓ navigate   ⏎ open   b backup everything   r re-probe   ? help[/]",
            classes="dash-hint",
        )


class LockedView(Vertical):
    def __init__(self, service: Service, probe: Probe):
        super().__init__(classes="locked")
        self.service = service
        self.probe = probe

    def compose(self) -> ComposeResult:
        state = self.probe.state
        colour = STATE_COLOR.get(state, "#ff6b6b")
        text = Text()
        text.append(f"{self.service.icon}  ", style=f"bold {colour}")
        text.append(self.service.label.upper(), style="bold #e6edf6")
        text.append("\n\n")
        text.append(STATE_WORD.get(state, state.value).upper(), style=f"bold {colour}")
        text.append("\n\n")
        text.append(self.probe.detail or "Nothing can be read here right now.", style="#c9d4e3")
        text.append("\n\n")
        if state is ServiceState.DENIED:
            hint = (
                "Grant the service account a role that covers this product "
                "(Firebase Admin, Editor, or a product-specific IAM role), then press r to re-check."
            )
        elif state is ServiceState.DISABLED:
            hint = (
                "The API is turned off for this project. Enable it in Google Cloud console → "
                "APIs & Services, wait a minute, then press r."
            )
        elif state is ServiceState.EMPTY:
            hint = "This product has not been set up in the project yet — there is nothing to read."
        else:
            hint = "Press r to retry, or check the service in the Firebase console."
        text.append(hint, style="#7d8899")
        yield Static(text, classes="lock-body")


class ServiceView(Vertical):
    DEFAULT_CSS = """
    ServiceView { height: 1fr; }
    ServiceView #split { height: 1fr; }
    ServiceView #split TreeBrowser { width: 46%; }
    ServiceView #split PreviewPane { width: 1fr; }
    ServiceView #stack { height: 1fr; }
    ServiceView #stack TableBrowser { height: 3fr; }
    ServiceView #stack PreviewPane { height: 2fr; }
    """

    def __init__(self, session: Session, key: str):
        super().__init__()
        self.session = session
        self.service = session.services[key]
        self.probe = session.probes.get(key, Probe())
        self.browser: TreeBrowser | TableBrowser | None = None
        self.preview: PreviewPane | None = None

    def compose(self) -> ComposeResult:
        if self.probe.state is not ServiceState.OK:
            yield LockedView(self.service, self.probe)
            return
        if self.service.tree:
            with Horizontal(id="split"):
                self.browser = TreeBrowser(self.service)
                yield self.browser
                self.preview = PreviewPane()
                yield self.preview
        else:
            with Vertical(id="stack"):
                self.browser = TableBrowser(self.service)
                yield self.browser
                self.preview = PreviewPane()
                yield self.preview

    # -- selection -----------------------------------------------------
    def on_item_highlighted(self, message: ItemHighlighted) -> None:
        if self.preview is None:
            return
        item = message.item
        if item is None:
            self.preview.clear()
            return
        self.preview.message("loading…")
        self.run_worker(lambda: self._load_preview(item), thread=True, exclusive=True, group="preview")

    def _load_preview(self, item: Item) -> None:
        try:
            preview = self.service.preview(item)
        except Exception as exc:  # noqa: BLE001 - previews are best-effort
            preview, error = None, str(exc)
        else:
            error = ""
        self.app.call_from_thread(self._show_preview, preview, error)

    def _show_preview(self, preview, error: str) -> None:
        if self.preview is None:
            return
        if error:
            self.preview.message(f"⚠ {error[:300]}", "error")
        elif preview is not None:
            self.preview.show(preview)

    def on_load_failed(self, message: LoadFailed) -> None:
        self.app.notify(message.message[:400], title="Could not load", severity="error", timeout=8)

    # -- actions -------------------------------------------------------
    @property
    def selected(self) -> Item | None:
        return self.browser.selected if self.browser else None

    def refresh_view(self) -> None:
        if isinstance(self.browser, TreeBrowser):
            self.browser.reload()
        elif isinstance(self.browser, TableBrowser):
            self.browser.load(self.browser.path)
        if self.preview:
            self.preview.clear()

    def load_more(self) -> bool:
        if isinstance(self.browser, TableBrowser):
            return self.browser.load_more()
        return False

    def go_up(self) -> bool:
        if isinstance(self.browser, TableBrowser):
            return self.browser.go_up()
        return False

    def open_selected(self) -> None:
        item = self.selected
        if item and isinstance(self.browser, TableBrowser):
            target = self.browser.open(item)
            if target is not None:
                self.browser.load(target)

    def focus_search(self) -> None:
        if isinstance(self.browser, TableBrowser):
            self.browser.focus_search()

    def download_selected(self) -> None:
        item = self.selected
        if item is None:
            self.app.notify("Nothing selected to download.", severity="warning")
            return
        rel = download_relpath(self.service.key, item)
        if rel is None:
            self.app.notify("Folders are downloaded with b (backup) or by opening them.", severity="warning")
            return
        dest = Path(self.app.download_root) / self.session.project_id / self.service.key / rel
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            index = 2
            while dest.exists():
                dest = dest.with_name(f"{stem}-{index}{suffix}")
                index += 1
        self.app.notify(f"Downloading {item.label}…", title=self.service.label, timeout=3)
        self.run_worker(lambda: self._download(item, dest), thread=True, group="download")

    def _download(self, item: Item, dest: Path) -> None:
        try:
            path = self.service.download(item, dest)
        except Exception as exc:  # noqa: BLE001 - report instead of crashing
            self.app.call_from_thread(self.app.notify, f"{exc}"[:400], title="Download failed", severity="error", timeout=10)
            return
        size = path.stat().st_size if path.is_file() else sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        self.app.call_from_thread(self.app.notify, f"{path}  ({size:,} bytes)", title="Download complete", timeout=10)
