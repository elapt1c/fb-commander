"""Configuration: API endpoints, on-disk locations, environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "fb-commander"
DEFAULT_BACKUP_ROOT = Path(os.environ.get("FB_CMD_BACKUP_ROOT", "~/firebase-backups")).expanduser()


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def credentials_dir() -> Path:
    return config_dir() / "credentials"


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Endpoints:
    """Google API roots. Every one is overridable so the whole stack can be
    pointed at a fixture server (that is how the end-to-end tests run)."""

    firebase: str = field(default_factory=lambda: _env("FB_CMD_FIREBASE_API", "https://firebase.googleapis.com"))
    rtdb_admin: str = field(
        default_factory=lambda: _env("FB_CMD_RTDB_ADMIN_API", "https://firebasedatabase.googleapis.com")
    )
    firestore: str = field(
        default_factory=lambda: _env("FB_CMD_FIRESTORE_API", "https://firestore.googleapis.com")
    )
    identity: str = field(
        default_factory=lambda: _env("FB_CMD_IDENTITY_API", "https://identitytoolkit.googleapis.com")
    )
    storage: str = field(
        default_factory=lambda: _env("FB_CMD_STORAGE_API", "https://storage.googleapis.com")
    )
    remoteconfig: str = field(
        default_factory=lambda: _env("FB_CMD_REMOTECONFIG_API", "https://firebaseremoteconfig.googleapis.com")
    )
    hosting: str = field(
        default_factory=lambda: _env("FB_CMD_HOSTING_API", "https://firebasehosting.googleapis.com")
    )
    rules: str = field(
        default_factory=lambda: _env("FB_CMD_RULES_API", "https://firebaserules.googleapis.com")
    )
    serviceusage: str = field(
        default_factory=lambda: _env("FB_CMD_SERVICEUSAGE_API", "https://serviceusage.googleapis.com")
    )
    oauth: str = field(
        default_factory=lambda: _env("FB_CMD_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token")
    )


# Scopes requested when minting an access token from a service-account key.
# `cloud-platform` covers Firestore/Storage, the firebase.* scopes cover the
# management + RTDB APIs, identitytoolkit covers Auth user administration.
SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/firebase",
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/firebase.readonly",
    "https://www.googleapis.com/auth/identitytoolkit",
    "https://www.googleapis.com/auth/userinfo.email",
)
