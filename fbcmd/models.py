"""Shared value types for services and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ServiceState(str, Enum):
    CHECKING = "checking"
    OK = "ok"
    DENIED = "denied"
    DISABLED = "disabled"
    EMPTY = "empty"
    ERROR = "error"

    @property
    def usable(self) -> bool:
        return self in (ServiceState.OK, ServiceState.EMPTY)



@dataclass
class Probe:
    state: ServiceState = ServiceState.CHECKING
    detail: str = ""
    latency_ms: int = 0
    count: int | None = None


@dataclass(frozen=True)
class Item:
    """One navigable thing: an RTDB node, Firestore collection/document,
    storage bucket/prefix/object, auth user, parameter, site or ruleset."""

    key: str
    label: str
    kind: str
    leaf: bool = True
    detail: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def with_detail(self, detail: str) -> "Item":
        return Item(self.key, self.label, self.kind, self.leaf, detail, self.meta)


@dataclass
class Preview:
    title: str
    body: str
    language: str = "json"
    meta: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TablePage:
    title: str
    columns: list[str]
    rows: list[list[str]]
    items: list[Item] = field(default_factory=list)
    breadcrumbs: list[tuple[str, str]] = field(default_factory=list)
    next_token: str | None = None
    subtitle: str = ""
    note: str = ""


@dataclass
class SnapshotResult:
    service: str
    state: str
    files: int = 0
    bytes: int = 0
    error: str = ""
    counts: dict[str, Any] = field(default_factory=dict)
