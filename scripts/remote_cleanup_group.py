from __future__ import annotations

import argparse
import getpass
import os

import paramiko


def main() -> int:
    parser = argparse.ArgumentParser(description="Disable remote test mailboxes by group name.")
    parser.add_argument("--group", required=True)
    args = parser.parse_args()

    password = os.environ.get("MAILBOX_SSH_PASSWORD") or getpass.getpass("SSH password: ")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        "103.214.172.30",
        username="root",
        password=password,
        timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        script = r'''
import json
import os
import urllib.parse
import urllib.request

ROOT = "/opt/mailbox-system"
GROUP = os.environ["CLEANUP_GROUP"]

def env(path):
    data = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            data[k] = v
    return data

token = env(f"{ROOT}/mailbox-api.env")["ADMIN_API_KEY"]
headers = {"X-Admin-Token": token}
url = "http://127.0.0.1:18080/admin/mailboxes?" + urllib.parse.urlencode({"domain": "i7wap.xyz", "limit": 1000})
req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=30) as response:
    items = json.loads(response.read().decode())

disabled = 0
active_after = 0
for item in items:
    if item.get("group_name") == GROUP and item.get("status") == "active":
        req = urllib.request.Request(f"http://127.0.0.1:18080/admin/mailboxes/{item['id']}/disable", method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30):
            pass
        disabled += 1

req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=30) as response:
    items = json.loads(response.read().decode())
for item in items:
    if item.get("group_name") == GROUP and item.get("status") == "active":
        active_after += 1

print(f"group={GROUP}")
print(f"disabled={disabled}")
print(f"active_after={active_after}")
'''
        command = (
            "cat > /tmp/remote_cleanup_group.py <<'PY'\n"
            + script
            + "\nPY\n"
            + f"CLEANUP_GROUP={args.group!r} python3 /tmp/remote_cleanup_group.py; "
            + "rc=$?; rm -f /tmp/remote_cleanup_group.py; exit $rc"
        )
        _, stdout, stderr = client.exec_command(command, timeout=120)
        status = stdout.channel.recv_exit_status()
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
        return status
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
