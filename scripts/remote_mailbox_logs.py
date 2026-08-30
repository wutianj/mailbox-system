import getpass
import os

import paramiko


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
    commands = {
        "ps": "docker ps --filter 'name=mailbox' --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'",
        "front_logs": "docker logs --tail 160 mailbox-mailu-front 2>&1",
        "smtp_logs": "docker logs --tail 160 mailbox-mailu-smtp 2>&1",
        "admin_logs": "docker logs --tail 160 mailbox-mailu-admin 2>&1",
        "imap_logs": "docker logs --tail 160 mailbox-mailu-imap 2>&1",
        "antispam_logs": "docker logs --tail 120 mailbox-mailu-antispam 2>&1",
        "worker_logs": "docker logs --tail 120 mailbox-worker 2>&1",
    }
    for name, command in commands.items():
        print(f"\n===== {name} =====")
        _, stdout, stderr = client.exec_command(command, timeout=90)
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
finally:
    client.close()
