"""Small UI helpers: state colours, download paths, safe names."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.text import Text

from ..models import Item, ServiceState

STATE_COLOR = {
    ServiceState.OK: "#4dd0a7",
    ServiceState.EMPTY: "#e3b341",
    ServiceState.DENIED: "#ff6b6b",
    ServiceState.DISABLED: "#e3b341",
    ServiceState.ERROR: "#ff6b6b",
    ServiceState.CHECKING: "#7d8899",
}

STATE_GLYPH = {
    ServiceState.OK: "●",
    ServiceState.EMPTY: "◐",
    ServiceState.DENIED: "⊘",
    ServiceState.DISABLED: "⊘",
    ServiceState.ERROR: "▲",
    ServiceState.CHECKING: "◌",
}

STATE_WORD = {
    ServiceState.OK: "ready",
    ServiceState.EMPTY: "empty",
    ServiceState.DENIED: "no access",
    ServiceState.DISABLED: "api off",
    ServiceState.ERROR: "error",
    ServiceState.CHECKING: "checking",
}


def badge(state: ServiceState, detail: str = "") -> Text:
    text = Text()
    text.append(STATE_GLYPH.get(state, "?"), style=STATE_COLOR.get(state, "#7d8899"))
    text.append(" ")
    text.append(STATE_WORD.get(state, state.value), style=f"{STATE_COLOR.get(state, '#7d8899')}")
    if detail:
        text.append(f"  {detail}", style="#7d8899")
    return text


_UNSAFE = re.compile(r'[<>:"|?*\x00-\x1f]')


def safe_segment(value: str, limit: int = 120) -> str:
    cleaned = _UNSAFE.sub("_", str(value)).replace("/", "_").replace("\\", "_").strip(" .")
    if cleaned in ("", ".", ".."):
        cleaned = "_"
    return cleaned[:limit]


def download_relpath(service_key: str, item: Item) -> Path | None:
    """Where a single-item download lands, relative to the service folder."""
    if service_key == "rtdb":
        parts = [safe_segment(p) for p in item.key.strip("/").split("/") if p]
        return Path(*parts).with_suffix(".json") if parts else Path("root.json")
    if service_key == "firestore":
        try:
            ids = json.loads(item.key)
        except (TypeError, ValueError):
            return None
        parts = [safe_segment(i) for i in ids]
        if len(ids) % 2 == 1:
            return Path(*parts)
        return Path(*parts).with_suffix(".json")
    if service_key == "storage":
        if item.kind != "object":
            return None
        return Path(safe_segment(item.meta.get("bucket", "bucket"))) / Path(
            *[safe_segment(p) for p in str(item.meta.get("name", "")).split("/") if p]
        )
    if service_key == "auth":
        return Path(safe_segment(item.key)).with_suffix(".json")
    if service_key == "rules":
        return Path(safe_segment(str(item.key).rsplit("/", 1)[-1])).with_suffix("")
    if service_key == "hosting":
        return Path(safe_segment(item.label)).with_suffix(".json")
    return Path(safe_segment(item.label)).with_suffix(".json")
