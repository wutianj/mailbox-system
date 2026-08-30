from __future__ import annotations

import argparse
import re
from pathlib import Path

import requests
import urllib3


ADMIN_SECRET_PATH = Path(r"D:\AccountSecrets\mailbox-system\admin.txt")
BASE_URL = "https://mail.i7wap.xyz"


def _read_admin_credentials() -> tuple[str, str]:
    raw = ADMIN_SECRET_PATH.read_text(encoding="utf-8", errors="replace")
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw)
    if not email_match:
        raise RuntimeError("admin email not found")

    password = None
    for line in raw.splitlines():
        if re.search(r"(?i)(mailu|admin|后台|邮箱|web).*(password|密码)|^(password|密码)", line) and (
            ":" in line or "：" in line
        ):
            value = re.split(r"[:：]", line, 1)[1].strip()
            if value and "***" not in value:
                password = value
                break
    if not password:
        for line in raw.splitlines():
            if re.search(r"(?i)password|密码", line) and (":" in line or "：" in line):
                value = re.split(r"[:：]", line, 1)[1].strip()
                if value and "***" not in value:
                    password = value
                    break
    if not password:
        raise RuntimeError("admin password not found")

    return email_match.group(0), password


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Mailu-authenticated /box page.")
    parser.add_argument("--generate-count", type=int, default=0)
    parser.add_argument("--group", default="box-ui-verify")
    args = parser.parse_args()

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    email, password = _read_admin_credentials()

    session = requests.Session()
    login = session.post(
        f"{BASE_URL}/sso/login?url=/admin/announcement",
        data={"email": email, "pw": password, "submitAdmin": ""},
        allow_redirects=True,
        timeout=30,
        verify=False,
    )
    sidebar = session.get(f"{BASE_URL}/admin/announcement", timeout=30, verify=False)
    box = session.get(f"{BASE_URL}/box", timeout=30, verify=False)
    export = session.post(f"{BASE_URL}/box/export", json={"domain": "i7wap.xyz"}, timeout=30, verify=False)
    generate = None
    generate_body = {}
    generate_no_prefix = True
    generate_format_ok = True
    if args.generate_count:
        generate = session.post(
            f"{BASE_URL}/box/generate",
            json={
                "domain": "i7wap.xyz",
                "count": args.generate_count,
                "prefix": "",
                "group_name": args.group,
                "label": "browser verify cleanup",
                "sync_with_mail_backend": True,
            },
            timeout=120,
            verify=False,
        )
        if generate.headers.get("content-type", "").startswith("application/json"):
            generate_body = generate.json()
        if generate.status_code == 200:
            for item in generate_body.get("items", []):
                local_part = item["email"].split("@", 1)[0]
                if not re.fullmatch(r"[a-z][a-z0-9]{11}", local_part):
                    generate_no_prefix = False
                if "----https://mail.i7wap.xyz/mail/" not in generate_body.get("export", ""):
                    generate_format_ok = False

    sidebar_has_box_link = 'href="/box"' in sidebar.text or "href='/box'" in sidebar.text

    print(f"login_status={login.status_code}")
    print(f"sidebar_status={sidebar.status_code}")
    print(f"sidebar_has_generate={'生成邮箱' in sidebar.text}")
    print(f"sidebar_has_box_link={sidebar_has_box_link}")
    print(f"box_status={box.status_code}")
    print(f"box_has_page={'批量生成邮箱' in box.text}")
    print(f"box_has_token_input={'X-Admin-Token' in box.text or '管理令牌' in box.text}")
    print(f"export_status={export.status_code}")
    print(f"export_is_attachment={'attachment;' in export.headers.get('Content-Disposition', '')}")
    print(f"export_count={export.headers.get('X-Mailbox-Count', '')}")
    if generate is not None:
        print(f"generate_status={generate.status_code}")
        print(f"generate_count={generate_body.get('count', '')}")
        print(f"generate_no_prefix={generate_no_prefix}")
        print(f"generate_format_ok={generate_format_ok}")

    ok = all(
        [
            login.status_code == 200,
            sidebar.status_code == 200,
            "生成邮箱" in sidebar.text,
            sidebar_has_box_link,
            box.status_code == 200,
            "批量生成邮箱" in box.text,
            "X-Admin-Token" not in box.text,
            "管理令牌" not in box.text,
            export.status_code == 200,
            "attachment;" in export.headers.get("Content-Disposition", ""),
        ]
    )
    if generate is not None:
        ok = ok and all(
            [
                generate.status_code == 200,
                generate_body.get("count") == args.generate_count,
                generate_no_prefix,
                generate_format_ok,
            ]
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
