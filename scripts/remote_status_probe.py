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
    commands = {
        "caddy_status": "docker ps --filter name=momo-link-caddy --format '{{.Names}} {{.Status}}'",
        "mailbox_marker": "grep -n 'mailbox-system' /opt/momo-link/public/Caddyfile || true",
        "caddy_top": "sed -n '1,60p' /opt/momo-link/public/Caddyfile",
        "mailbox_root": "find /opt/mailbox-system -maxdepth 2 -type f 2>/dev/null | sort | sed -n '1,80p'",
    }
    for name, command in commands.items():
        print(f"\n===== {name} =====")
        _, stdout, stderr = client.exec_command(command, timeout=60)
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
finally:
    client.close()
