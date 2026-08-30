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
        "ops_export": "python3 /opt/mailbox-system/ops/export_mailboxes.py --domain i7wap.xyz",
        "containers": "docker ps --filter name=mailbox --format '{{.Names}} {{.Status}}'",
        "listeners": "ss -lntp '( sport = :25 or sport = :80 or sport = :443 or sport = :18080 or sport = :18088 )' 2>/dev/null || true",
        "dns_remote": "getent hosts mail.i7wap.xyz || true",
    }
    for name, command in commands.items():
        print(f"\n===== {name} =====")
        _, stdout, stderr = client.exec_command(command, timeout=120)
        status = stdout.channel.recv_exit_status()
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
        print(f"exit={status}")
finally:
    client.close()
