from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class CloudflareClientError(RuntimeError):
    def __init__(self, action: str, status_code: int | None, message: str) -> None:
        self.action = action
        self.status_code = status_code
        super().__init__(f"{action}: {status_code or 'transport'} {message}")


@dataclass(frozen=True)
class CloudflareClient:
    api_token: str
    account_id: str | None = None
    timeout_seconds: float = 20.0
    api_base_url: str = "https://api.cloudflare.com/client/v4"

    def find_best_zone(self, fqdn: str) -> dict[str, Any] | None:
        for zone_name in _candidate_zone_names(fqdn):
            data = self._request("GET", "/zones", params={"name": zone_name, "per_page": 1})
            results = data.get("result") if isinstance(data.get("result"), list) else []
            if results:
                return results[0]
        return None

    def create_zone(self, domain: str) -> dict[str, Any]:
        if not self.account_id:
            raise CloudflareClientError("create_zone", None, "CLOUDFLARE_ACCOUNT_ID is required")
        data = self._request(
            "POST",
            "/zones",
            json={"name": domain, "account": {"id": self.account_id}, "type": "full"},
        )
        result = data.get("result")
        if not isinstance(result, dict):
            raise CloudflareClientError("create_zone", None, "Cloudflare returned no zone result")
        return result

    def upsert_dns_record(
        self,
        *,
        zone_id: str,
        record_type: str,
        name: str,
        content: str,
        ttl: int = 1,
        proxied: bool | None = None,
        priority: int | None = None,
        match_content_prefix: str | None = None,
    ) -> str:
        record_type = record_type.upper()
        normalized_name = name.rstrip(".")
        payload: dict[str, Any] = {
            "type": record_type,
            "name": normalized_name,
            "content": content.rstrip(".") if record_type in {"A", "AAAA", "CNAME", "MX"} else content,
            "ttl": ttl,
        }
        if proxied is not None and record_type in {"A", "AAAA", "CNAME"}:
            payload["proxied"] = proxied
        if priority is not None and record_type == "MX":
            payload["priority"] = priority

        existing = self._dns_records(zone_id, record_type, normalized_name)
        exact = next((record for record in existing if _record_matches(record, payload)), None)
        if exact:
            return "unchanged"

        target = None
        if match_content_prefix:
            target = next((record for record in existing if str(record.get("content", "")).startswith(match_content_prefix)), None)
        elif existing:
            target = existing[0]

        if target:
            record_id = str(target["id"])
            self._request("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", json=payload)
            return "updated"

        self._request("POST", f"/zones/{zone_id}/dns_records", json=payload)
        return "created"

    def _dns_records(self, zone_id: str, record_type: str, name: str) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"type": record_type, "name": name, "per_page": 100},
        )
        results = data.get("result")
        return results if isinstance(results, list) else []

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_token}",
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.request(
                    method,
                    f"{self.api_base_url.rstrip('/')}{path}",
                    headers=headers,
                    json=json,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise CloudflareClientError(path, None, str(exc)) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise CloudflareClientError(path, response.status_code, response.text[:500]) from exc
        if response.status_code >= 400 or data.get("success") is False:
            errors = data.get("errors") if isinstance(data.get("errors"), list) else []
            message = "; ".join(str(item.get("message", item)) for item in errors) or response.text[:500]
            raise CloudflareClientError(path, response.status_code, message)
        return data if isinstance(data, dict) else {}


def configured_cloudflare_client(
    *,
    enabled: bool,
    api_token: str | None,
    account_id: str | None,
    timeout_seconds: float,
) -> CloudflareClient | None:
    if not enabled:
        return None
    if not api_token:
        raise CloudflareClientError("configure", None, "CLOUDFLARE_API_TOKEN is required")
    return CloudflareClient(api_token=api_token, account_id=account_id, timeout_seconds=timeout_seconds)


def _candidate_zone_names(fqdn: str) -> list[str]:
    parts = [part for part in fqdn.lower().rstrip(".").split(".") if part]
    candidates = [".".join(parts[index:]) for index in range(max(len(parts) - 1, 0))]
    return [candidate for candidate in candidates if "." in candidate]


def _record_matches(record: dict[str, Any], payload: dict[str, Any]) -> bool:
    if str(record.get("content", "")).rstrip(".") != str(payload.get("content", "")).rstrip("."):
        return False
    if "priority" in payload and record.get("priority") != payload["priority"]:
        return False
    if "proxied" in payload and record.get("proxied") is not payload["proxied"]:
        return False
    return True
