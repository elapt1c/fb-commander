"""Lazy tree browser for Realtime Database and Firestore."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree
from textual.widgets._tree import TreeNode

from ...models import Item
from ...services.base import Service


class ItemHighlighted(Message):
    def __init__(self, item: Item | None) -> None:
        super().__init__()
        self.item = item


class LoadFailed(Message):
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message


class TreeBrowser(Vertical):
    DEFAULT_CSS = """
    TreeBrowser { height: 1fr; }
    TreeBrowser #tree { height: 1fr; }
    """

    def __init__(self, service: Service):
        super().__init__()
        self.service = service
        self.loaded: set[str] = set()
        self.selected: Item | None = None

    def compose(self) -> ComposeResult:
        yield Static("STRUCTURE", classes="pane-title")
        yield Tree(f"{self.service.label}", id="tree")

    def on_mount(self) -> None:
        tree = self.query_one("#tree", Tree)
        tree.root.expand()
        self.call_after_refresh(tree.focus)
        self.reload()

    def reload(self) -> None:
        self.loaded.clear()
        tree = self.query_one("#tree", Tree)
        tree.root.remove_children()
        self.selected = None
        self.post_message(ItemHighlighted(None))
        self.run_worker(self._fetch_roots, thread=True, exclusive=True, group="tree-roots")

    def _fetch_roots(self) -> None:
        try:
            items = self.service.tree_roots()
        except Exception as exc:  # noqa: BLE001 - surface to the user, never crash the app
            self.app.call_from_thread(self.post_message, LoadFailed(str(exc)))
            return
        self.app.call_from_thread(self._apply_roots, items)

    def _apply_roots(self, items: list[Item]) -> None:
        tree = self.query_one("#tree", Tree)
        tree.root.remove_children()
        for item in items:
            self._add(tree.root, item)
        tree.root.expand()
        if not items:
            tree.root.add_leaf("(empty)")

    def _add(self, parent: TreeNode, item: Item) -> None:
        node = parent.add(item.label, data=item, expand=False)
        if not item.leaf:
            node.add_leaf("loading…", data=None)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        item = node.data
        if not isinstance(item, Item) or item.leaf or item.key in self.loaded:
            return
        self.loaded.add(item.key)
        self.run_worker(
            lambda: self._fetch_children(item, node),
            thread=True,
            group=f"tree-{hash(item.key)}",
        )

    def _fetch_children(self, item: Item, node: TreeNode) -> None:
        try:
            children = self.service.tree_children(item)
            error = ""
        except Exception as exc:  # noqa: BLE001 - show the failure at the node
            children, error = [], str(exc)
        self.app.call_from_thread(self._apply_children, node, item, children, error)

    def _apply_children(self, node: TreeNode, item: Item, children: list[Item], error: str) -> None:
        if not node.is_expanded:
            return
        node.remove_children()
        if error:
            node.add_leaf(f"⚠ {error[:120]}", data=None)
            return
        if not children:
            node.add_leaf("(empty)", data=None)
            return
        for child in children:
            self._add(node, child)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        item = event.node.data
        self.selected = item if isinstance(item, Item) else None
        self.post_message(ItemHighlighted(self.selected))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        item = event.node.data
        if isinstance(item, Item):
            self.selected = item
            self.post_message(ItemHighlighted(item))
