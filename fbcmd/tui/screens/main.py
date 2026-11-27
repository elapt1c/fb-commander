"""Main screen: service rail, project dashboard and per-service views."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ...api import probe_error
from ...backup import BackupPlan
from ...models import Probe, ServiceState
from ...session import Session
from ..util import STATE_COLOR, badge
from ..views import DashboardView, ServiceView
from .backup import BackupDialog, BackupRunner
from .help import HelpScreen

DASHBOARD = "dashboard"


class MainScreen(Screen):
    BINDINGS = [
        Binding("b", "backup", "Backup", show=True, priority=True),
        Binding("d", "download", "Download", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("n", "more", "More", show=False),
        Binding("enter", "open_item", "Open", show=False),
        Binding("escape", "focus_rail", "Rail", show=False),
        Binding("backspace", "up", "Up", show=False),
        Binding("slash", "search", "Search", show=False, key_display="/"),
        Binding("question_mark", "help", "Help", show=True, key_display="?"),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_key", "New key", show=False),
    ]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.current = DASHBOARD
        self.view: DashboardView | ServiceView | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(id="brand")
            yield Static(id="topright")
        with Horizontal(id="body"):
            with Vertical(id="rail"):
                yield Static("SERVICES", classes="rail-title")
                yield OptionList(id="nav")
                yield Static(id="railfoot")
            with Vertical(id="pane"):
                yield Static(id="pane-head")
                yield Vertical(id="viewport")
        yield Footer()

    # -- lifecycle -----------------------------------------------------
    def on_mount(self) -> None:
        self._paint_topbar()
        self._build_nav()
        self.query_one("#nav", OptionList).focus()
        self.show_service(DASHBOARD)

    def _paint_topbar(self) -> None:
        brands = Text()
        brands.append("FB", style="bold #ffa000")
        brands.append(" · COMMANDER", style="bold #e6edf6")
        brands.append("   ", style="")
        brands.append(self.session.project_id, style="#8fa1b7")
        self.query_one("#brand", Static).update(brands)

        ready = sum(1 for p in self.session.probes.values() if p.state.usable)
        right = Text()
        right.append(f"{ready}/{len(self.session.services)} services reachable", style="#7d8899")
        right.append("\n")
        right.append(self.session.account.client_email, style="#4a5a6e")
        self.query_one("#topright", Static).update(right)

        foot = Text()
        foot.append("backups\n", style="bold #5d6b7e")
        foot.append(str(self.app.backup_root), style="#7d8899")
        foot.append("\n")
        foot.append("press ? for keys", style="#3d4a5c")
        self.query_one("#railfoot", Static).update(foot)

    def _build_nav(self) -> None:
        nav = self.query_one("#nav", OptionList)
        nav.clear_options()
        nav.add_option(Option(self._nav_prompt("◆", "Overview", "#ffa000", ServiceState.OK, ""), id=DASHBOARD))
        nav.add_option(None)  # divider
        for key, service in self.session.services.items():
            probe = self.session.probes.get(key, Probe())
            nav.add_option(
                Option(self._nav_prompt(service.icon, service.label, service.accent, probe.state, probe.detail), id=key)
            )

    @staticmethod
    def _nav_prompt(icon: str, label: str, accent: str, state: ServiceState, detail: str) -> Text:
        color = STATE_COLOR.get(state, "#7d8899")
        text = Text()
        text.append(f"{icon} ", style=f"bold {accent}")
        text.append(f"{label:<20}", style=("#e6edf6" if state.usable or state is ServiceState.OK else "#7d8899"))
        text.append(" ")
        text.append({"ok": "●", "denied": "⊘", "disabled": "⊘", "error": "▲", "empty": "◐", "checking": "◌"}.get(state.value, "?"), style=color)
        return text

    # -- navigation ----------------------------------------------------
    def show_service(self, key: str) -> None:
        self.current = key
        viewport = self.query_one("#viewport", Vertical)
        viewport.remove_children()
        if key == DASHBOARD:
            view = DashboardView(self.session)
            self.query_one("#pane-head", Static).update(self._dash_head())
        else:
            service = self.session.services[key]
            probe = self.session.probes.get(key, Probe())
            view = ServiceView(self.session, key)
            self.query_one("#pane-head", Static).update(self._service_head(service, probe))
        self.view = view
        viewport.mount(view)

    def _dash_head(self) -> Text:
        text = Text()
        text.append("OVERVIEW", style="bold #ffa000")
        text.append("   ", style="")
        text.append("everything this key can see, at a glance", style="#7d8899")
        return text

    def _service_head(self, service, probe: Probe) -> Text:
        text = Text()
        text.append(f"{service.icon} ", style=f"bold {service.accent}")
        text.append(service.label, style="bold #e6edf6")
        text.append("   ", style="")
        text.append(service.blurb, style="#7d8899")
        text.append("      ", style="")
        text.append(badge(probe.state, probe.detail))
        return text

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.show_service(event.option_id)

    # -- actions -------------------------------------------------------
    def action_backup(self) -> None:
        self.app.push_screen(BackupDialog(self.session), callback=self._backup_chosen)

    def _backup_chosen(self, plan: BackupPlan | None) -> None:
        if plan is not None:
            self.app.push_screen(BackupRunner(self.session, plan))

    def action_download(self) -> None:
        if isinstance(self.view, ServiceView):
            self.view.download_selected()
        else:
            self.app.notify("Open a service first, then press d on an item.", severity="warning")

    def action_more(self) -> None:
        if isinstance(self.view, ServiceView) and not self.view.load_more():
            self.app.notify("Nothing more to load here.", severity="information", timeout=2)

    def action_open_item(self) -> None:
        if isinstance(self.view, ServiceView):
            self.view.open_selected()

    def action_up(self) -> None:
        if isinstance(self.view, ServiceView) and not self.view.go_up():
            self.show_service(DASHBOARD)

    def action_search(self) -> None:
        if isinstance(self.view, ServiceView):
            self.view.focus_search()

    def action_refresh(self) -> None:
        if self.current == DASHBOARD:
            self._reprobe(None)
            return
        if isinstance(self.view, ServiceView):
            key = self.view.service.key
            self.app.notify(f"Re-checking {self.session.services[key].label}…", timeout=2)
            self._reprobe(key)
        else:
            self.show_service(self.current)

    def _reprobe(self, key: str | None) -> None:
        def work() -> None:
            keys = [key] if key else list(self.session.services)
            for item in keys:
                try:
                    probe = self.session.services[item].probe()
                except Exception as exc:  # noqa: BLE001 - probe failure is a state
                    state, detail = probe_error(exc)
                    probe = Probe(ServiceState(state), detail)
                self.session.probes[item] = probe
            self.app.call_from_thread(self._reprobed)

        self.run_worker(work, thread=True, exclusive=True, group="reprobe")

    def _reprobed(self) -> None:
        self._paint_topbar()
        self._build_nav()
        self.show_service(self.current)
        self.app.notify("Access map refreshed", timeout=3)

    def action_focus_rail(self) -> None:
        self.query_one("#nav", OptionList).focus()

    def action_new_key(self) -> None:
        from .connect import ConnectScreen

        self.app.push_screen(ConnectScreen())

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()
