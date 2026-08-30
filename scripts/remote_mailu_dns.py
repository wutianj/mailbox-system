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
TOKEN="$(awk -F= '$1=="API_TOKEN"{print $2}' /opt/mailbox-system/mailu.env)"
curl -fsS -X POST -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:18088/api/v1/domain/i7wap.xyz/dkim >/dev/null || true
curl -fsS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:18088/api/v1/domain/i7wap.xyz
'''
    _, stdout, stderr = client.exec_command(script, timeout=60)
    print(stdout.read().decode(errors="replace").strip())
    err = stderr.read().decode(errors="replace").strip()
    if err:
        print("[stderr]\n" + err)
finally:
    client.close()
