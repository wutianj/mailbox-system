from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class MailuClientError(RuntimeError):
    def __init__(self, action: str, status_code: int | None, message: str) -> None:
        self.action = action
        self.status_code = status_code
        super().__init__(f"{action}: {status_code or 'transport'} {message}")


@dataclass(frozen=True)
class MailuClient:
    api_url: str
    api_token: str
    timeout_seconds: float = 10.0

    def ensure_domain(self, domain: str) -> None:
        payload = {
            "name": domain,
            "comment": "managed by mailbox-api",
            "max_users": -1,
            "max_aliases": -1,
            "max_quota_bytes": 0,
            "signup_enabled": False,
        }
        self._request("POST", "/domain", json=payload, ok_statuses={200, 409})

    def get_domain(self, domain: str) -> dict[str, Any]:
        return self._request("GET", f"/domain/{quote(domain, safe='')}", ok_statuses={200})

    def generate_dkim(self, domain: str) -> None:
        self._request("POST", f"/domain/{quote(domain, safe='')}/dkim", ok_statuses={200})

    def create_user(
        self,
        *,
        email: str,
        password: str,
        quota_bytes: int,
        enable_imap: bool,
        enable_pop: bool,
        spam_enabled: bool,
    ) -> None:
        payload: dict[str, Any] = {
            "email": email,
            "raw_password": password,
            "comment": "managed by mailbox-api",
            "quota_bytes": quota_bytes,
            "global_admin": False,
            "enabled": True,
            "change_pw_next_login": False,
            "enable_imap": enable_imap,
            "enable_pop": enable_pop,
            "allow_spoofing": False,
            "forward_enabled": False,
            "reply_enabled": False,
            "displayed_name": email,
            "spam_enabled": spam_enabled,
            "spam_mark_as_read": True,
        }
        self._request("POST", "/user", json=payload, ok_statuses={200})

    def set_user_enabled(self, email: str, enabled: bool) -> None:
        self._request("PATCH", f"/user/{quote(email, safe='')}", json={"enabled": enabled}, ok_statuses={200})

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        ok_statuses: set[int],
    ) -> dict[str, Any]:
        url = f"{self.api_url.rstrip('/')}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_token}",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.request(method, url, headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise MailuClientError(path, None, str(exc)) from exc
        if response.status_code not in ok_statuses:
            body = response.text[:500]
            raise MailuClientError(path, response.status_code, body)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


def configured_mailu_client(
    *,
    enabled: bool,
    api_url: str,
    api_token: str | None,
    timeout_seconds: float,
) -> MailuClient | None:
    if not enabled:
        return None
    if not api_token:
        raise MailuClientError("configure", None, "MAILU_API_TOKEN is required when MAILU_SYNC_ENABLED=true")
    return MailuClient(api_url=api_url, api_token=api_token, timeout_seconds=timeout_seconds)
