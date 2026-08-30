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
set -eu
before="$(ls -1t /opt/mailbox-system/exports/*i7wap.xyz-1.txt 2>/dev/null | head -n1 || true)"
python3 /opt/mailbox-system/ops/generate_mailboxes.py --count 1 --prefix opscheck --group ops-check >/tmp/ops_generate_probe.out
cat /tmp/ops_generate_probe.out
export_path="$(awk -F= '$1=="export_path"{print $2}' /tmp/ops_generate_probe.out)"
if [ ! -f "$export_path" ]; then
  echo "export_file=missing"
  exit 10
fi
line_count="$(wc -l < "$export_path" | tr -d ' ')"
format_count="$(grep -Ec '^[^@[:space:]]+@i7wap\.xyz----https://mail\.i7wap\.xyz/mail/[^[:space:]]+$' "$export_path")"
echo "line_count=$line_count"
echo "format_count=$format_count"
if [ "$line_count" != "1" ] || [ "$format_count" != "1" ]; then
  exit 11
fi
cat > /tmp/disable_ops_check.py <<'PY'
import json
import urllib.parse
import urllib.request

def read_env(path):
    data = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            data[k] = v
    return data

token = read_env("/opt/mailbox-system/mailbox-api.env")["ADMIN_API_KEY"]
headers = {"X-Admin-Token": token}
url = "http://127.0.0.1:18080/admin/mailboxes?" + urllib.parse.urlencode({"domain": "i7wap.xyz", "limit": 1000})
with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
    items = json.loads(response.read().decode())
disabled = 0
for item in items:
    if item.get("group_name") == "ops-check" and item.get("status") == "active":
        req = urllib.request.Request(f"http://127.0.0.1:18080/admin/mailboxes/{item['id']}/disable", method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30):
            pass
        disabled += 1
print(f"ops_check_disabled={disabled}")
PY
python3 /tmp/disable_ops_check.py
rm -f /tmp/disable_ops_check.py /tmp/ops_generate_probe.out
test -x /opt/mailbox-system/ROLLBACK.sh && echo "rollback_executable=yes"
'''
    _, stdout, stderr = client.exec_command(script, timeout=180)
    status = stdout.channel.recv_exit_status()
    print(stdout.read().decode(errors="replace").strip())
    err = stderr.read().decode(errors="replace").strip()
    if err:
        print("[stderr]\n" + err)
    raise SystemExit(status)
finally:
    client.close()
