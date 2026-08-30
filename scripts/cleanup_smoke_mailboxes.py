import getpass

import paramiko


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    "103.214.172.30",
    username="root",
    password=getpass.getpass("SSH password: "),
    timeout=20,
    look_for_keys=False,
    allow_agent=False,
)
try:
    script = r'''
import json
import urllib.parse
import urllib.request

ROOT = "/opt/mailbox-system"

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
active = 0
for item in items:
    if item.get("group_name") == "deploy-smoke":
        req = urllib.request.Request(f"http://127.0.0.1:18080/admin/mailboxes/{item['id']}/disable", method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30):
            pass
        disabled += 1
    elif item.get("status") == "active":
        active += 1

print(f"smoke_disabled={disabled}")
print(f"non_smoke_active={active}")
'''
    command = f"cat > /tmp/cleanup_smoke_mailboxes.py <<'PY'\n{script}\nPY\npython3 /tmp/cleanup_smoke_mailboxes.py; rc=$?; rm -f /tmp/cleanup_smoke_mailboxes.py; exit $rc"
    _, stdout, stderr = client.exec_command(command, timeout=120)
    status = stdout.channel.recv_exit_status()
    print(stdout.read().decode(errors="replace").strip())
    err = stderr.read().decode(errors="replace").strip()
    if err:
        print("[stderr]\n" + err)
    raise SystemExit(status)
finally:
    client.close()
