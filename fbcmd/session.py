"""One connected Firebase project: credentials, API client, service instances."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .api import Api
from .auth import Account, TokenProvider
from .config import Endpoints
from .models import Probe, ServiceState
from .services import Ctx, Service, build_services


@dataclass
class Session:
    account: Account
    endpoints: Endpoints
    tokens: TokenProvider
    api: Api
    services: dict[str, Service]
    probes: dict[str, Probe] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        account: Account,
        endpoints: Endpoints | None = None,
        static_token: str | None = None,
    ) -> "Session":
        endpoints = endpoints or Endpoints()
        tokens = TokenProvider(account, static_token=static_token)
        api = Api(tokens, endpoints)
        services = build_services(Ctx(project_id=account.project_id, api=api, endpoints=endpoints))
        return cls(account=account, endpoints=endpoints, tokens=tokens, api=api, services=services)

    @property
    def project_id(self) -> str:
        return self.account.project_id

    def service(self, key: str) -> Service:
        return self.services[key]

    def probe_all(self, on_result: Callable[[str, Probe], None] | None = None) -> dict[str, Probe]:
        """Probe every service concurrently; never raises (errors become states)."""

        def run(key: str, service: Service) -> tuple[str, Probe]:
            try:
                return key, service.probe()
            except Exception as exc:  # noqa: BLE001 - a probe failure is data, not a crash
                from .api import probe_error

                state, detail = probe_error(exc)
                return key, Probe(ServiceState(state), detail)

        keys = list(self.services)
        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
            futures = {pool.submit(run, key, self.services[key]): key for key in keys}
            for future in as_completed(futures):
                key, probe = future.result()
                self.probes[key] = probe
                if on_result:
                    on_result(key, probe)
        # Deferred services depend on others (RTDB discovery hits the admin API);
        # keep probes in sidebar order for stable rendering.
        return {key: self.probes[key] for key in keys if key in self.probes}

    def usable(self) -> list[str]:
        return [key for key, probe in self.probes.items() if probe.state.usable]
