"""Service-account credential parsing, persistence and access tokens."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import SCOPES, credentials_dir
from .errors import AUTH, ApiError

REQUIRED = ("type", "project_id", "private_key", "client_email")


class CredentialError(ValueError):
    """Raised for a paste that is not a usable service-account key."""


@dataclass
class Account:
    info: dict
    source: Path | None = None

    @property
    def project_id(self) -> str:
        return str(self.info.get("project_id", ""))

    @property
    def client_email(self) -> str:
        return str(self.info.get("client_email", ""))

    @property
    def private_key_id(self) -> str:
        return str(self.info.get("private_key_id", ""))

    @classmethod
    def from_text(cls, text: str) -> "Account":
        text = (text or "").strip()
        if not text:
            raise CredentialError("Nothing pasted — drop the service-account JSON in the box.")
        if text.startswith("{"):
            try:
                info = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CredentialError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
        elif Path(text).expanduser().is_file():
            return cls.from_file(Path(text).expanduser())
        else:
            raise CredentialError("That is not JSON. Paste the whole service-account key file (starting with '{').")

        if not isinstance(info, dict):
            raise CredentialError("Service-account JSON must be an object.")
        missing = [k for k in REQUIRED if not info.get(k)]
        if missing:
            raise CredentialError(f"Missing required field(s): {', '.join(missing)}")
        if info.get("type") != "service_account":
            raise CredentialError(f"Expected type 'service_account', got {info.get('type')!r}.")
        key = str(info.get("private_key", ""))
        if "BEGIN PRIVATE KEY" not in key or "END PRIVATE KEY" not in key:
            raise CredentialError("private_key is not a PEM key — the paste was probably truncated.")
        return cls(info=info)

    @classmethod
    def from_file(cls, path: Path) -> "Account":
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialError(f"Cannot read {path}: {exc}") from exc
        if not isinstance(info, dict):
            raise CredentialError(f"{path} is not a service-account JSON object.")
        missing = [k for k in REQUIRED if not info.get(k)]
        if missing:
            raise CredentialError(f"{path} is missing: {', '.join(missing)}")
        return cls(info=info, source=path)

    def check_private_key(self) -> None:
        """Local PEM sanity check so a truncated paste fails before any network call."""
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError:  # pragma: no cover - cryptography ships with google-auth
            return
        try:
            serialization.load_pem_private_key(str(self.info["private_key"]).encode(), password=None)
        except Exception as exc:  # noqa: BLE001 - any parse failure is a bad paste
            raise CredentialError(
                "private_key is not a valid RSA key — the paste is probably truncated or "
                "the quotes/escapes were mangled. Download the JSON again (Firebase console → "
                "Project settings → Service accounts → Generate new private key)."
            ) from exc

    def save(self) -> Path:
        """Persist to the private credentials dir (0600). Never world-readable."""
        directory = credentials_dir()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        path = directory / f"{self.project_id}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.info, handle, indent=2)
        os.chmod(path, 0o600)
        self.source = path
        return path

    def redacted(self) -> dict:
        safe = {k: v for k, v in self.info.items() if k not in ("private_key", "private_key_id")}
        return safe


def load_saved_accounts() -> list[Account]:
    directory = credentials_dir()
    accounts: list[Account] = []
    if not directory.is_dir():
        return accounts
    for path in sorted(directory.glob("*.json")):
        try:
            accounts.append(Account.from_file(path))
        except CredentialError:
            continue
    return accounts


class TokenProvider:
    """Mints OAuth2 access tokens from the key, refreshing shortly before expiry.

    ``FB_CMD_ACCESS_TOKEN`` short-circuits minting entirely (used by tests and
    by people who already hold a token from the gcloud CLI).
    """

    def __init__(self, account: Account, static_token: str | None = None):
        self.account = account
        self._static = static_token or os.environ.get("FB_CMD_ACCESS_TOKEN") or None
        self._lock = threading.Lock()
        self._creds = None
        self._token = ""
        self._expiry = 0.0

    def _build(self):
        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise ApiError(0, AUTH, "google-auth is not installed; re-run ./run.sh to repair the venv.") from exc
        try:
            return service_account.Credentials.from_service_account_info(self.account.info, scopes=list(SCOPES))
        except Exception as exc:  # noqa: BLE001 - any parse problem is a credential problem
            raise ApiError(0, AUTH, f"Service-account key rejected: {exc}") from exc

    def access_token(self, force: bool = False) -> str:
        if self._static:
            return self._static
        with self._lock:
            if not force and self._token and time.time() < self._expiry - 120:
                return self._token
            if self._creds is None:
                self._creds = self._build()
            try:
                import google.auth.transport.requests as gtrans

                self._creds.refresh(gtrans.Request())
            except Exception as exc:  # noqa: BLE001 - network or revoked key, surfaced as auth error
                raise ApiError(0, AUTH, f"Could not mint an access token: {exc}") from exc
            if not self._creds.token:
                raise ApiError(0, AUTH, "Google returned an empty access token.")
            self._token = str(self._creds.token)
            expiry = getattr(self._creds, "expiry", None)
            self._expiry = expiry.timestamp() if expiry else time.time() + 3000
            return self._token
