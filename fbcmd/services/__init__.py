"""Service registry. Order here is the order the sidebar shows."""

from __future__ import annotations

from .base import Ctx, Service, Unsupported, human_bytes, safe_join, write_json
from .firestore import FirestoreService
from .hosting import HostingService
from .identity import IdentityService
from .project import ProjectService
from .remoteconfig import RemoteConfigService
from .rtdb import RtdbService
from .rules import RulesService
from .storage import StorageService

SERVICE_CLASSES = (
    ProjectService,
    FirestoreService,
    RtdbService,
    IdentityService,
    StorageService,
    RemoteConfigService,
    HostingService,
    RulesService,
)


def build_services(ctx: Ctx) -> dict[str, Service]:
    return {cls.key: cls(ctx) for cls in SERVICE_CLASSES}


__all__ = [
    "Ctx",
    "Service",
    "Unsupported",
    "build_services",
    "SERVICE_CLASSES",
    "human_bytes",
    "safe_join",
    "write_json",
]
