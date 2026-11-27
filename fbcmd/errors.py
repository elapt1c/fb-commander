"""Error types and Google API error classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Kinds the UI understands. Everything that is not OK is rendered dimmed and
# excluded from "backup everything".
PERMISSION = "permission"
NOT_FOUND = "not_found"
DISABLED = "disabled"
AUTH = "auth"
QUOTA = "quota"
NETWORK = "network"
HTTP = "http"


@dataclass
class ApiError(Exception):
    status: int
    kind: str
    message: str
    url: str = ""

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.status} {self.kind}: {self.message}"


def _clean(message: str) -> str:
    message = re.sub(r"\s+", " ", message or "").strip()
    return message[:400]


def classify(status: int, payload: object, url: str = "") -> ApiError:
    """Turn a Google error body into a typed ApiError.

    Shape is usually ``{"error": {"code": 403, "message": "...",
    "details": [{"reason": "..."}]}}`` but RTDB returns a bare string.
    """
    message = ""
    reason = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
            for detail in err.get("details") or []:
                if isinstance(detail, dict) and detail.get("reason"):
                    reason = str(detail["reason"])
                    break
            reason = reason or str(err.get("status") or "")
        elif isinstance(err, str):
            message = err
        message = message or str(payload.get("error_description") or payload.get("message") or "")
    elif isinstance(payload, str):
        message = payload

    haystack = f"{reason} {message}".lower()
    if status in (401,) or "unauthenticated" in haystack or "invalid_grant" in haystack:
        kind = AUTH
    elif status == 403 or "permission_denied" in haystack or "permission denied" in haystack:
        kind = PERMISSION
    elif status == 404 or "not_found" in haystack:
        kind = NOT_FOUND
    elif status == 429 or "quota" in haystack or "rate limit" in haystack:
        kind = QUOTA
    elif "service_disabled" in haystack or "has not been used in project" in haystack or "api is not enabled" in haystack:
        kind = DISABLED
    else:
        kind = HTTP
    return ApiError(status=status, kind=kind, message=_clean(message) or f"HTTP {status}", url=url)
