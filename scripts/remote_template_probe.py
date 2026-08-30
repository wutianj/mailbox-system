from __future__ import annotations

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

commands = {
    "remote_sidebar": "grep -n 'anonalias_list\\|生成邮箱\\|href=\"/box\"' /opt/mailbox-system/mailu/overrides/admin/sidebar.html || true",
    "container_sidebar": "docker exec mailbox-mailu-admin sh -lc 'grep -n '\\''anonalias_list\\|生成邮箱\\|href=\"/box\"'\\'' /app/mailu/ui/templates/sidebar.html || true'",
    "mounts": "docker inspect mailbox-mailu-admin --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'",
    "admin_log_tail": "docker logs --tail=40 mailbox-mailu-admin 2>&1",
}

try:
    for name, command in commands.items():
        print(f"===== {name} =====")
        _, stdout, stderr = client.exec_command(command, timeout=60)
        status = stdout.channel.recv_exit_status()
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
        print(f"exit={status}")
finally:
    client.close()
