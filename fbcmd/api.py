"""HTTP layer: retries, auth header refresh, typed errors, streaming downloads."""

from __future__ import annotations

import time
from typing import Any

import requests

from .auth import TokenProvider
from .config import Endpoints
from .errors import AUTH, HTTP, NETWORK, QUOTA, ApiError, classify

RETRY_STATUS = (429, 500, 502, 503, 504)


class Api:
    def __init__(self, tokens: TokenProvider, endpoints: Endpoints, timeout: float = 45.0):
        self.tokens = tokens
        self.endpoints = endpoints
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "fb-commander/1.0 (+tui)"

    # -- low level -----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.access_token()}"}

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        stream: bool = False,
        retries: int = 2,
        reauth: bool = True,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        last: Exception | None = None
        while attempt <= retries:
            merged = self._headers()
            if headers:
                merged.update(headers)
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=merged,
                    timeout=self.timeout,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last = exc
                if attempt == retries:
                    raise ApiError(0, NETWORK, f"Network error contacting {url}: {exc}", url) from exc
                time.sleep(0.6 * (2**attempt))
                attempt += 1
                continue

            if response.status_code < 400:
                return response
            if response.status_code == 401 and reauth:
                # Token might have been revoked or clock-skewed: refresh once.
                self.tokens.access_token(force=True)
                attempt += 1
                continue
            if response.status_code in RETRY_STATUS and attempt < retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").replace(".", "").isdigit() else 0.6 * (2**attempt)
                response.close()
                time.sleep(min(delay, 10.0))
                attempt += 1
                continue

            payload = self._payload(response)
            response.close()
            raise classify(response.status_code, payload, url)
        raise ApiError(0, NETWORK, f"Giving up on {url} after retries: {last}", url)

    @staticmethod
    def _payload(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            try:
                return response.text[:800]
            except Exception:  # noqa: BLE001 - body already gone
                return ""

    # -- helpers -------------------------------------------------------
    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", url, params=params).json()

    def post_json(self, url: str, body: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self.request("POST", url, json_body=body, params=params).json()

    def download(self, url: str, dest, params: dict[str, Any] | None = None, chunk: int = 1 << 20) -> int:
        """Stream a (possibly large) object to ``dest``; returns bytes written."""
        response = self.request("GET", url, params=params, stream=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with dest.open("wb") as handle:
            for block in response.iter_content(chunk):
                if block:
                    handle.write(block)
                    written += len(block)
        response.close()
        return written


def probe_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to (ui-state, short human detail)."""
    from .models import ServiceState

    if isinstance(exc, ApiError):
        if exc.kind == "permission":
            return ServiceState.DENIED.value, exc.message
        if exc.kind == "disabled":
            return ServiceState.DISABLED.value, exc.message
        if exc.kind in ("auth", "network", "quota", "not_found", "http"):
            return ServiceState.ERROR.value, f"{exc.kind}: {exc.message}"
    return ServiceState.ERROR.value, str(exc)[:300]


__all__ = ["Api", "probe_error", "AUTH", "QUOTA", "HTTP", "NETWORK"]
