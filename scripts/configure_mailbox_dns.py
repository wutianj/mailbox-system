from __future__ import annotations

import getpass
import json
import re
import runpy
import urllib.parse
from pathlib import Path

import paramiko


ZONE = "i7wap.xyz"
MAIL_HOST = "mail.i7wap.xyz"
MAIL_IP = "103.214.172.30"
CF_HELPER = Path(r"C:\Users\Administrator\.codex\tmp\cf_i7wap_configure.py")


def main() -> int:
    ssh_password = getpass.getpass("SSH password: ")
    mailu_dns = fetch_mailu_dns(ssh_password)

    helper = runpy.run_path(str(CF_HELPER))
    token, source, checked = helper["find_valid_token"]()
    print(f"cloudflare_tokens_checked={len(checked)}")
    if source:
        print(f"cloudflare_token_source={source}")
    if not token:
        print("cloudflare_token=not_found")
        return 2
    request = helper["cf_request"]

    zone_id = get_zone_id(request, token)
    changes: list[str] = []

    upsert_record(
        request,
        token,
        zone_id,
        {"type": "A", "name": MAIL_HOST, "content": MAIL_IP, "ttl": 1, "proxied": False},
        changes,
    )

    mx = parse_mx(mailu_dns["dns_mx"])
    upsert_record(
        request,
        token,
        zone_id,
        {"type": "MX", "name": mx["name"], "content": mx["content"], "ttl": 1, "priority": mx["priority"], "proxied": False},
        changes,
    )
    delete_other_mx(request, token, zone_id, mx["name"], mx["content"], changes)

    for key in ("dns_spf", "dns_dkim", "dns_dmarc", "dns_dmarc_report"):
        value = mailu_dns.get(key)
        if not value:
            continue
        txt = parse_txt(value)
        upsert_txt(request, token, zone_id, txt["name"], txt["content"], changes)

    for line in mailu_dns.get("dns_autoconfig") or []:
        cname = parse_cname(line)
        if cname:
            upsert_record(
                request,
                token,
                zone_id,
                {"type": "CNAME", "name": cname["name"], "content": cname["content"], "ttl": 1, "proxied": False},
                changes,
            )

    print("dns_changes=" + ",".join(changes))
    return 0


def fetch_mailu_dns(password: str) -> dict:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        MAIL_IP,
        username="root",
        password=password,
        timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        command = r'''
set -eu
TOKEN="$(awk -F= '$1=="API_TOKEN"{print $2}' /opt/mailbox-system/mailu.env)"
curl -fsS -X POST -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:18088/api/v1/domain/i7wap.xyz/dkim >/dev/null || true
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:18088/api/v1/domain/i7wap.xyz
'''
        _, stdout, stderr = client.exec_command(command, timeout=60)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("mailu_dns_stderr=" + err.splitlines()[-1])
        return json.loads(out)
    finally:
        client.close()


def get_zone_id(request, token: str) -> str:
    status, payload = request(token, "GET", f"/zones?name={ZONE}&per_page=10")
    if status != 200 or not payload.get("success") or not payload.get("result"):
        raise RuntimeError(f"zone_lookup_failed status={status}")
    zone = payload["result"][0]
    print(f"zone_found={zone.get('name')} status={zone.get('status')}")
    return zone["id"]


def upsert_txt(request, token: str, zone_id: str, name: str, content: str, changes: list[str]) -> None:
    query = urllib.parse.urlencode({"type": "TXT", "name": name, "per_page": 100})
    status, payload = request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    if status != 200 or not payload.get("success"):
        raise RuntimeError(f"txt_lookup_failed name={name} status={status}")
    records = payload.get("result", [])
    target = next((record for record in records if normalized_txt(record.get("content", "")) == normalized_txt(content)), None)
    if target:
        changes.append(f"TXT:{name}:unchanged")
        return
    if records:
        record = records[0]
        status, updated = request(token, "PUT", f"/zones/{zone_id}/dns_records/{record['id']}", {
            "type": "TXT",
            "name": name,
            "content": content,
            "ttl": 1,
        })
        if status != 200 or not updated.get("success"):
            raise RuntimeError(f"txt_update_failed name={name} status={status}")
        for duplicate in records[1:]:
            request(token, "DELETE", f"/zones/{zone_id}/dns_records/{duplicate['id']}")
        changes.append(f"TXT:{name}:updated")
        return
    upsert_record(request, token, zone_id, {"type": "TXT", "name": name, "content": content, "ttl": 1}, changes)


def upsert_record(request, token: str, zone_id: str, body: dict, changes: list[str]) -> None:
    query = urllib.parse.urlencode({"type": body["type"], "name": body["name"], "per_page": 100})
    status, payload = request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    if status != 200 or not payload.get("success"):
        raise RuntimeError(f"record_lookup_failed {body['type']} {body['name']} status={status}")
    records = payload.get("result", [])
    if records:
        record = records[0]
        same = record.get("content", "").rstrip(".") == str(body.get("content", "")).rstrip(".")
        if body["type"] == "MX":
            same = same and int(record.get("priority", 0)) == int(body.get("priority", 0))
        if body["type"] in {"A", "CNAME"}:
            same = same and record.get("proxied") is False
        if same:
            changes.append(f"{body['type']}:{body['name']}:unchanged")
            return
        status, updated = request(token, "PUT", f"/zones/{zone_id}/dns_records/{record['id']}", body)
        action = "updated"
        result = updated
    else:
        status, result = request(token, "POST", f"/zones/{zone_id}/dns_records", body)
        action = "created"
    if status not in (200, 201) or not result.get("success"):
        raise RuntimeError(f"record_{action}_failed {body['type']} {body['name']} status={status}")
    changes.append(f"{body['type']}:{body['name']}:{action}")


def delete_other_mx(request, token: str, zone_id: str, name: str, keep_content: str, changes: list[str]) -> None:
    query = urllib.parse.urlencode({"type": "MX", "name": name, "per_page": 100})
    status, payload = request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    if status != 200 or not payload.get("success"):
        raise RuntimeError(f"mx_cleanup_lookup_failed status={status}")
    for record in payload.get("result", []):
        if record.get("content", "").rstrip(".") == keep_content.rstrip("."):
            continue
        status, deleted = request(token, "DELETE", f"/zones/{zone_id}/dns_records/{record['id']}")
        if status not in (200, 202) or not deleted.get("success"):
            raise RuntimeError(f"mx_delete_failed status={status}")
        changes.append(f"MX:{name}:deleted:{record.get('content')}")


def parse_mx(line: str) -> dict:
    match = re.match(r"^(\S+)\.\s+\d+\s+IN\s+MX\s+(\d+)\s+(\S+)\.$", line)
    if not match:
        raise ValueError(f"invalid_mx={line}")
    return {"name": match.group(1), "priority": int(match.group(2)), "content": match.group(3)}


def parse_txt(line: str) -> dict:
    match = re.match(r"^(\S+)\.\s+\d+\s+IN\s+TXT\s+\"(.*)\"$", line)
    if not match:
        raise ValueError(f"invalid_txt={line[:80]}")
    return {"name": match.group(1), "content": match.group(2)}


def parse_cname(line: str) -> dict | None:
    match = re.match(r"^(\S+)\.\s+\d+\s+IN\s+CNAME\s+(\S+)\.$", line)
    if not match:
        return None
    return {"name": match.group(1), "content": match.group(2)}


def normalized_txt(value: str) -> str:
    return value.strip().strip('"')


if __name__ == "__main__":
    raise SystemExit(main())
