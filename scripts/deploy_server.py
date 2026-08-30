from __future__ import annotations

import getpass
import os
import posixpath
import secrets
import shlex
import string
import time
from pathlib import Path

import paramiko


HOST = "103.214.172.30"
USER = "root"
PORT = 22
REMOTE_ROOT = "/opt/mailbox-system"
CADDYFILE = "/opt/momo-link/public/Caddyfile"
PUBLIC_HOST = "mail.i7wap.xyz"
MAIL_DOMAIN = "i7wap.xyz"
MAIL_HOST_IP = "103.214.172.30"
MAILBOX_SUBNET = "172.31.214.0/24"
MAILBOX_API_PORT = "18080"
MAILU_HTTP_PORT = "18088"
MAILU_VERSION = "2024.06"

LOCAL_ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {"upstream", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", "data"}
EXCLUDE_FILES = {"test-mailbox-api.db"}


def main() -> int:
    password = os.environ.get("MAILBOX_SSH_PASSWORD") or getpass.getpass("SSH password: ")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=password,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    sftp = client.open_sftp()
    release_id = time.strftime("%Y%m%d%H%M%S")
    try:
        prepare_remote(client, release_id)
        upload_release(sftp, client, release_id)
        write_environment(sftp)
        install_caddy_route(sftp, client, release_id)
        run_remote(client, f"chmod +x {REMOTE_ROOT}/ROLLBACK.sh")
        run_remote(client, f"cd {REMOTE_ROOT} && docker compose --env-file .env -f compose.yaml config --quiet", timeout=120)
        run_remote(client, f"cd {REMOTE_ROOT} && docker compose --env-file .env -f compose.yaml up -d --build", timeout=1200)
        run_remote(client, f"cd {REMOTE_ROOT} && docker compose --env-file .env -f compose.yaml restart admin antispam smtp front", timeout=300)
        wait_for_remote_services(client)
        run_smoke(client, timeout=180)
        print("DEPLOY_RESULT=ok")
        print(f"REMOTE_ROOT={REMOTE_ROOT}")
        print("PUBLIC_HOST=mail.i7wap.xyz")
        print("ADMIN_API=127.0.0.1:18080")
        return 0
    finally:
        sftp.close()
        client.close()


def prepare_remote(client: paramiko.SSHClient, release_id: str) -> None:
    quoted_root = shlex.quote(REMOTE_ROOT)
    run_remote(
        client,
        "\n".join(
            [
                "set -eu",
                f"mkdir -p {quoted_root}/.backups/{release_id}",
                f"mkdir -p {quoted_root}/mailu/overrides/nginx {quoted_root}/mailu/overrides/dovecot {quoted_root}/mailu/overrides/postfix {quoted_root}/mailu/overrides/rspamd {quoted_root}/mailu/overrides/roundcube",
                f"if [ -f {quoted_root}/compose.yaml ]; then cp -a {quoted_root}/compose.yaml {quoted_root}/.backups/{release_id}/compose.yaml; fi",
                f"if [ -d {quoted_root}/api ]; then tar -C {quoted_root} -czf {quoted_root}/.backups/{release_id}/api.tgz api; fi",
                f"rm -rf {quoted_root}/api.upload.{release_id}",
                f"mkdir -p {quoted_root}/api.upload.{release_id}",
            ]
        ),
    )


def upload_release(sftp: paramiko.SFTPClient, client: paramiko.SSHClient, release_id: str) -> None:
    upload_dir(sftp, LOCAL_ROOT / "api", f"{REMOTE_ROOT}/api.upload.{release_id}")
    upload_dir(sftp, LOCAL_ROOT / "ops", f"{REMOTE_ROOT}/ops")
    upload_dir(sftp, LOCAL_ROOT / "deploy" / "overrides", f"{REMOTE_ROOT}/mailu/overrides")
    put_file(sftp, LOCAL_ROOT / "deploy" / "compose.yaml", f"{REMOTE_ROOT}/compose.yaml")
    put_file(sftp, LOCAL_ROOT / "deploy" / "caddy-mailbox.caddy", f"{REMOTE_ROOT}/caddy-mailbox.caddy")
    run_remote(
        client,
        "\n".join(
            [
                "set -eu",
                f"rm -rf {shlex.quote(REMOTE_ROOT)}/api",
                f"mv {shlex.quote(REMOTE_ROOT)}/api.upload.{release_id} {shlex.quote(REMOTE_ROOT)}/api",
            ]
        ),
    )


def write_environment(sftp: paramiko.SFTPClient) -> None:
    server_env = read_env(sftp, f"{REMOTE_ROOT}/.env")
    api_env = read_env(sftp, f"{REMOTE_ROOT}/mailbox-api.env")
    mailu_env = read_env(sftp, f"{REMOTE_ROOT}/mailu.env")

    db_password = server_env.get("MAILBOX_DB_PASSWORD") or token_urlsafe()
    admin_api_key = api_env.get("ADMIN_API_KEY") or token_urlsafe(32)
    app_secret = api_env.get("APP_SECRET") or token_urlsafe(48)
    mailu_api_token = mailu_env.get("API_TOKEN") or api_env.get("MAILU_API_TOKEN") or token_urlsafe(32)
    mailu_secret_key = mailu_env.get("SECRET_KEY") or alpha_num(16)
    initial_admin_pw = mailu_env.get("INITIAL_ADMIN_PW") or token_urlsafe(24)
    cloudflare_api_token = api_env.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    cloudflare_account_id = api_env.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    cloudflare_sync_enabled = (
        api_env.get("CLOUDFLARE_SYNC_ENABLED")
        or os.environ.get("CLOUDFLARE_SYNC_ENABLED")
        or ("true" if cloudflare_api_token else "false")
    )

    mailbox_api_lines = [
        f"DATABASE_URL=postgresql://mailbox:{db_password}@mailbox-db:5432/mailbox",
        "REDIS_URL=redis://mailbox-redis:6379/0",
        f"API_BASE_URL=https://{PUBLIC_HOST}",
        f"DEFAULT_DOMAIN={MAIL_DOMAIN}",
        f"MAIL_PUBLIC_IP={MAIL_HOST_IP}",
        f"ADMIN_API_KEY={admin_api_key}",
        f"APP_SECRET={app_secret}",
        "TOKEN_BYTES=24",
        "MAILBOX_SECRET_BYTES=18",
        "MAILBOX_QUOTA_BYTES=0",
        "MAILU_SYNC_ENABLED=true",
        "MAILU_ADMIN_API_URL=http://front/api/v1",
        "MAILU_ADMIN_AUTH_URL=http://admin:8080/internal/auth/admin",
        f"MAILU_API_TOKEN={mailu_api_token}",
        "MAILU_TIMEOUT_SECONDS=15",
        "MAILU_IMAP_HOST=front",
        "MAILU_IMAP_PORT=143",
        "MAILU_IMAP_SSL=false",
        "MAILU_IMAP_TIMEOUT_SECONDS=12",
        "WORKER_POLL_SECONDS=15",
        "WORKER_CONCURRENCY=8",
        "WORKER_BATCH_SIZE=32",
        "WORKER_FETCH_LIMIT=10",
        "MAIL_READ_REFRESH_ENABLED=true",
        "DB_POOL_SIZE=30",
        "DB_MAX_OVERFLOW=120",
        "BOX_REQUIRE_MAILU_ADMIN_SESSION=true",
        f"CLOUDFLARE_SYNC_ENABLED={cloudflare_sync_enabled}",
        "CLOUDFLARE_TIMEOUT_SECONDS=20",
    ]
    if cloudflare_api_token:
        mailbox_api_lines.append(f"CLOUDFLARE_API_TOKEN={cloudflare_api_token}")
    if cloudflare_account_id:
        mailbox_api_lines.append(f"CLOUDFLARE_ACCOUNT_ID={cloudflare_account_id}")
    mailbox_api_lines.append("")

    write_text(
        sftp,
        f"{REMOTE_ROOT}/.env",
        "\n".join(
            [
                f"MAILBOX_DB_PASSWORD={db_password}",
                f"MAILBOX_API_PORT={MAILBOX_API_PORT}",
                f"MAILU_HTTP_PORT={MAILU_HTTP_PORT}",
                f"MAIL_HOST_IP={MAIL_HOST_IP}",
                f"MAILBOX_DOCKER_SUBNET={MAILBOX_SUBNET}",
                f"MAILU_VERSION={MAILU_VERSION}",
                "",
            ]
        ),
        mode=0o600,
    )
    write_text(
        sftp,
        f"{REMOTE_ROOT}/mailbox-api.env",
        "\n".join(mailbox_api_lines),
        mode=0o600,
    )
    write_text(
        sftp,
        f"{REMOTE_ROOT}/mailu.env",
        "\n".join(
            [
                f"SECRET_KEY={mailu_secret_key}",
                f"SUBNET={MAILBOX_SUBNET}",
                f"DOMAIN={MAIL_DOMAIN}",
                f"HOSTNAMES={PUBLIC_HOST}",
                "POSTMASTER=postmaster",
                "TLS_FLAVOR=notls",
                "PORTS=25,80,143",
                "AUTH_RATELIMIT_IP=10000/hour",
                "AUTH_RATELIMIT_USER=100000/day",
                "DISABLE_STATISTICS=True",
                "ADMIN=true",
                "WEBMAIL=roundcube",
                "API=true",
                "WEBDAV=none",
                "ANTIVIRUS=clamav",
                "SCAN_MACROS=false",
                "MESSAGE_SIZE_LIMIT=50000000",
                "MESSAGE_RATELIMIT=100000/day",
                "RELAYNETS=",
                "RELAYHOST=",
                "FETCHMAIL_ENABLED=False",
                "RECIPIENT_DELIMITER=+",
                "DMARC_RUA=postmaster",
                "DMARC_RUF=postmaster",
                "DMARC_SEND_REPORTS=false",
                "WELCOME=false",
                "WELCOME_SUBJECT=Welcome",
                "WELCOME_BODY=Welcome",
                "COMPRESSION=none",
                "COMPRESSION_LEVEL=6",
                "FULL_TEXT_SEARCH=en",
                "WEBROOT_REDIRECT=/admin",
                "WEB_ADMIN=/admin",
                "WEB_WEBMAIL=/webmail",
                "WEB_API=/api",
                "SITENAME=Mailbox System",
                f"WEBSITE=https://{PUBLIC_HOST}",
                "COMPOSE_PROJECT_NAME=mailbox-system",
                "CREDENTIAL_ROUNDS=12",
                "REAL_IP_HEADER=X-Forwarded-For",
                f"REAL_IP_FROM={MAILBOX_SUBNET},127.0.0.1/32",
                "REJECT_UNLISTED_RECIPIENT=yes",
                "LOG_LEVEL=INFO",
                "TZ=Asia/Shanghai",
                "DEFAULT_SPAM_THRESHOLD=80",
                f"API_TOKEN={mailu_api_token}",
                "FULL_TEXT_SEARCH_ATTACHMENTS=false",
                "INITIAL_ADMIN_ACCOUNT=postmaster",
                f"INITIAL_ADMIN_DOMAIN={MAIL_DOMAIN}",
                f"INITIAL_ADMIN_PW={initial_admin_pw}",
                "INITIAL_ADMIN_MODE=ifmissing",
                "",
            ]
        ),
        mode=0o600,
    )


def install_caddy_route(sftp: paramiko.SFTPClient, client: paramiko.SSHClient, release_id: str) -> None:
    current = read_text(sftp, CADDYFILE)
    backup_path = f"{CADDYFILE}.mailbox-system.{release_id}.bak"
    write_text(sftp, backup_path, current, mode=0o644)
    block = read_text(sftp, f"{REMOTE_ROOT}/caddy-mailbox.caddy").strip() + "\n"
    begin = "# mailbox-system begin"
    end = "# mailbox-system end"
    if begin in current and end in current:
        before, rest = current.split(begin, 1)
        _, after = rest.split(end, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip()
    else:
        updated = current.rstrip() + "\n\n" + block
    if updated != current:
        write_text(sftp, CADDYFILE, updated, mode=0o644)
    rollback = "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            f"cd {REMOTE_ROOT}",
            "docker compose --env-file .env -f compose.yaml down || true",
            f"cp -f {backup_path} {CADDYFILE}",
            "docker exec momo-link-caddy caddy validate --config /etc/caddy/Caddyfile",
            "docker exec momo-link-caddy caddy reload --config /etc/caddy/Caddyfile",
            "",
        ]
    )
    write_text(sftp, f"{REMOTE_ROOT}/ROLLBACK.sh", rollback, mode=0o700)
    try:
        run_remote(client, "docker exec momo-link-caddy caddy validate --config /etc/caddy/Caddyfile", timeout=120)
        apply_caddy_config(client)
    except RuntimeError:
        write_text(sftp, CADDYFILE, current, mode=0o644)
        run_remote(client, "docker exec momo-link-caddy caddy validate --config /etc/caddy/Caddyfile", timeout=120)
        apply_caddy_config(client)
        raise


def apply_caddy_config(client: paramiko.SSHClient) -> None:
    try:
        run_remote(client, "docker exec momo-link-caddy caddy reload --config /etc/caddy/Caddyfile", timeout=120)
    except RuntimeError:
        run_remote(client, "docker restart momo-link-caddy", timeout=120)


def wait_for_remote_services(client: paramiko.SSHClient) -> None:
    run_remote(
        client,
        r'''
set -eu
deadline=$(($(date +%s) + 120))
while [ "$(date +%s)" -lt "$deadline" ]; do
  admin="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' mailbox-mailu-admin 2>/dev/null || true)"
  api="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' mailbox-api 2>/dev/null || true)"
  front="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' mailbox-mailu-front 2>/dev/null || true)"
  if [ "$admin" = "healthy" ] && [ "$api" = "healthy" ] && [ "$front" = "healthy" ] && curl -fsS http://127.0.0.1:18080/health >/dev/null; then
    echo "REMOTE_READY=ok"
    exit 0
  fi
  sleep 3
done
echo "REMOTE_READY=timeout admin=$admin api=$api front=$front"
exit 1
''',
        timeout=150,
    )


def run_smoke(client: paramiko.SSHClient, timeout: int) -> None:
    script = r'''
import json
import os
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = "/opt/mailbox-system"

def read_env(path):
    data = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k] = v
    return data

env = read_env(os.path.join(ROOT, "mailbox-api.env"))
admin = env["ADMIN_API_KEY"]
base = "http://127.0.0.1:18080"

def request(method, path, payload=None, headers=None, params=None, tries=1):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    req_headers = headers or {}
    if payload is not None:
        data = json.dumps(payload).encode()
        req_headers = dict(req_headers)
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
                text = body.decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return resp.status, text
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code >= 500 and attempt + 1 < tries:
                last = f"{exc.code} {body[:200]}"
                time.sleep(3)
                continue
            return exc.code, body
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise last

health_status, health = request("GET", "/health", tries=30)
print(f"SMOKE health_status={health_status} backend={health.get('mail_backend_enabled')}")

domain_payload = {
    "domain": "i7wap.xyz",
    "mx_host": "mail.i7wap.xyz",
    "api_base_url": "https://mail.i7wap.xyz",
    "sync_with_mail_backend": True,
}
domain_status, _ = request("POST", "/admin/domains", payload=domain_payload, headers={"X-Admin-Token": admin})
if domain_status not in (200, 409):
    raise SystemExit(f"domain_status={domain_status}")
print(f"SMOKE domain_status={domain_status}")

batch_payload = {
    "domain": "i7wap.xyz",
    "count": 2,
    "prefix": "smoke",
    "group_name": "deploy-smoke",
    "sync_with_mail_backend": True,
}
batch_status, batch = request("POST", "/admin/mailboxes/batch", payload=batch_payload, headers={"X-Admin-Token": admin}, tries=5)
if batch_status != 200:
    raise SystemExit(f"batch_status={batch_status}")
items = batch["items"]
first, second = items[0], items[1]
print(f"SMOKE batch_count={batch.get('count')}")

message_status, _ = request("POST", "/admin/messages", payload={
    "email": first["email"],
    "from_addr": "sender@gmail.com",
    "subject": "direct smoke",
    "text_body": "Your verification code is 482915."
}, headers={"X-Admin-Token": admin})
if message_status != 200:
    raise SystemExit(f"message_status={message_status}")

json_api_path = first["api_url"].replace("https://mail.i7wap.xyz", "") + "?format=json"
correct_status, correct = request("GET", json_api_path, tries=3)
if correct_status != 200 or correct.get("code") != "482915":
    raise SystemExit(f"correct_status={correct_status} code_match={correct.get('code') == '482915'}")
print("SMOKE direct_api=ok")

public_body = None
last_public_error = None
for _ in range(12):
    try:
        with urllib.request.urlopen(first["api_url"] + "?format=json", timeout=30) as resp:
            public_body = json.loads(resp.read().decode("utf-8", errors="replace"))
        break
    except Exception as exc:
        last_public_error = exc
        time.sleep(3)
if public_body is None:
    raise SystemExit(f"public_api_request_failed={last_public_error}")
if public_body.get("code") != "482915":
    raise SystemExit("public_api_code_match=false")
print("SMOKE public_api=ok")

wrong_status, _ = request("GET", "/api/mail/code", params={"email": first["email"], "token": second["token"]})
if wrong_status != 403:
    raise SystemExit(f"wrong_token_status={wrong_status}")
print("SMOKE isolation=ok")

smtp_message = (
    "From: sender@gmail.com\r\n"
    f"To: {first['email']}\r\n"
    "Subject: smtp smoke\r\n"
    "\r\n"
    "Your verification code is 654321.\r\n"
)
smtp_sent = False
last_smtp_error = None
for _ in range(18):
    try:
        with smtplib.SMTP("103.214.172.30", 25, timeout=20) as smtp:
            smtp.ehlo("example.net")
            smtp.sendmail("sender@gmail.com", [first["email"]], smtp_message)
        smtp_sent = True
        break
    except Exception as exc:
        last_smtp_error = exc
        time.sleep(5)
if not smtp_sent:
    raise SystemExit(f"smtp_send_failed={last_smtp_error}")
print("SMOKE smtp_send=accepted")

imap_seen = False
for _ in range(18):
    time.sleep(5)
    status, body = request("GET", json_api_path)
    if status == 200 and body.get("code") == "654321":
        imap_seen = True
        break
if not imap_seen:
    raise SystemExit("imap_worker_code_match=false")
print("SMOKE imap_worker=ok")

mailboxes_status, mailboxes = request("GET", "/admin/mailboxes", headers={"X-Admin-Token": admin}, params={"domain": "i7wap.xyz", "limit": 1000})
disabled = 0
if mailboxes_status == 200:
    email_to_id = {item["email"]: item["id"] for item in mailboxes}
    for item in items:
        mid = email_to_id.get(item["email"])
        if mid:
            status, _ = request("POST", f"/admin/mailboxes/{mid}/disable", headers={"X-Admin-Token": admin})
            if status == 200:
                disabled += 1
print(f"SMOKE disabled={disabled}")
'''
    remote_script = f"{REMOTE_ROOT}/smoke.py"
    quoted = shlex.quote(remote_script)
    run_remote(client, f"cat > {quoted} <<'PY'\n{script}\nPY\npython3 {quoted}; rc=$?; rm -f {quoted}; exit $rc", timeout=timeout)


def upload_dir(sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str) -> None:
    mkdir_p(sftp, remote_dir)
    for item in local_dir.iterdir():
        if item.name in EXCLUDE_DIRS or item.name in EXCLUDE_FILES:
            continue
        remote_path = posixpath.join(remote_dir, item.name)
        if item.is_dir():
            upload_dir(sftp, item, remote_path)
        elif item.is_file():
            put_file(sftp, item, remote_path)


def put_file(sftp: paramiko.SFTPClient, local_file: Path, remote_file: str) -> None:
    mkdir_p(sftp, posixpath.dirname(remote_file))
    sftp.put(str(local_file), remote_file)


def mkdir_p(sftp: paramiko.SFTPClient, path: str) -> None:
    parts = []
    while path not in ("", "/"):
        parts.append(path)
        path = posixpath.dirname(path)
    for part in reversed(parts):
        try:
            sftp.stat(part)
        except FileNotFoundError:
            sftp.mkdir(part)


def read_env(sftp: paramiko.SFTPClient, remote_file: str) -> dict[str, str]:
    try:
        content = read_text(sftp, remote_file)
    except FileNotFoundError:
        return {}
    data: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def read_text(sftp: paramiko.SFTPClient, remote_file: str) -> str:
    with sftp.open(remote_file, "rb") as handle:
        data = handle.read()
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def write_text(sftp: paramiko.SFTPClient, remote_file: str, content: str, *, mode: int) -> None:
    mkdir_p(sftp, posixpath.dirname(remote_file))
    with sftp.open(remote_file, "wb") as handle:
        handle.write(content.encode("utf-8"))
    sftp.chmod(remote_file, mode)


def run_remote(client: paramiko.SSHClient, command: str, *, timeout: int = 300) -> str:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out.strip():
        print(redact_output(out.rstrip()))
    if err.strip():
        print(redact_output(err.rstrip()))
    if exit_status != 0:
        raise RuntimeError(f"remote command failed status={exit_status}")
    return out


def token_urlsafe(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def alpha_num(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def redact_output(text: str) -> str:
    safe_lines = []
    secret_keys = (
        "PASSWORD=",
        "ADMIN_API_KEY=",
        "APP_SECRET=",
        "API_TOKEN=",
        "MAILU_API_TOKEN=",
        "CLOUDFLARE_API_TOKEN=",
        "CLOUDFLARE_ACCOUNT_ID=",
        "SECRET_KEY=",
        "INITIAL_ADMIN_PW=",
        "DATABASE_URL=",
    )
    for line in text.splitlines():
        if any(key in line for key in secret_keys):
            key = line.split("=", 1)[0]
            safe_lines.append(f"{key}=<redacted>")
        else:
            safe_lines.append(line)
    return "\n".join(safe_lines)


if __name__ == "__main__":
    raise SystemExit(main())
