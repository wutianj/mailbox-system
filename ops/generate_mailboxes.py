from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path("/opt/mailbox-system")
API = "http://127.0.0.1:18080"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Mailu-backed mailbox API links.")
    parser.add_argument("--domain", default="i7wap.xyz")
    parser.add_argument("--mx-host", default="mail.i7wap.xyz")
    parser.add_argument("--api-base-url", default="https://mail.i7wap.xyz")
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--group", default=None)
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    admin_token = read_env(ROOT / "mailbox-api.env")["ADMIN_API_KEY"]
    headers = {"X-Admin-Token": admin_token}

    domain_status, _ = request(
        "POST",
        "/admin/domains",
        headers=headers,
        payload={
            "domain": args.domain,
            "mx_host": args.mx_host,
            "api_base_url": args.api_base_url,
            "sync_with_mail_backend": True,
        },
    )
    if domain_status not in (200, 409):
        raise SystemExit(f"domain_status={domain_status}")

    status, body = request(
        "POST",
        "/admin/mailboxes/batch",
        headers=headers,
        payload={
            "domain": args.domain,
            "count": args.count,
            "prefix": args.prefix,
            "group_name": args.group,
            "label": args.label,
            "sync_with_mail_backend": True,
        },
    )
    if status != 200:
        raise SystemExit(f"batch_status={status} body={body}")

    export_dir = ROOT / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    export_path = export_dir / f"{stamp}-{args.domain}-{body['count']}.txt"
    export_path.write_text(body["export"] + "\n", encoding="utf-8")
    export_path.chmod(0o600)
    print(f"count={body['count']}")
    print(f"export_path={export_path}")
    return 0


def read_env(path: Path) -> dict[str, str]:
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def request(method: str, path: str, *, headers: dict[str, str], payload: dict | None = None) -> tuple[int, dict | str]:
    data = None
    req_headers = dict(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
