from __future__ import annotations

import getpass
import sys
from dataclasses import dataclass

import paramiko


HOST = "103.214.172.30"
PORT = 22
USER = "root"


@dataclass
class RemoteCommand:
    name: str
    command: str


COMMANDS = [
    RemoteCommand("identity", "whoami && hostname && uname -a"),
    RemoteCommand("os", "cat /etc/os-release | sed -n '1,8p'"),
    RemoteCommand("resources", "df -h / /opt 2>/dev/null; echo '---'; free -h"),
    RemoteCommand("docker", "docker --version 2>/dev/null || true; docker compose version 2>/dev/null || true; docker ps --format 'table {{.Names}}\\t{{.Image}}\\t{{.Ports}}\\t{{.Status}}' 2>/dev/null || true"),
    RemoteCommand("listeners", "ss -lntup 2>/dev/null | sed -n '1,80p'"),
    RemoteCommand("services", "for s in docker caddy nginx postfix dovecot mailu-admin mailu-front; do systemctl is-active $s 2>/dev/null | sed \"s/^/$s=/\"; done"),
    RemoteCommand("opt_dirs", "find /opt -maxdepth 2 -type d 2>/dev/null | sort | sed -n '1,120p'"),
    RemoteCommand(
        "caddy_routes",
        "for f in /etc/caddy/Caddyfile /opt/momo-link/public/Caddyfile; do "
        "if [ -f \"$f\" ]; then echo \"FILE=$f\"; "
        "awk '/^[A-Za-z0-9_.:-]+[[:space:]]*\\{/ {print} /reverse_proxy/ {print}' \"$f\"; fi; done",
    ),
]


def main() -> int:
    password = getpass.getpass("SSH password: ")
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
    try:
        for item in COMMANDS:
            print(f"\n===== {item.name} =====")
            stdin, stdout, stderr = client.exec_command(item.command, timeout=60)
            out = stdout.read().decode("utf-8", errors="replace").rstrip()
            err = stderr.read().decode("utf-8", errors="replace").rstrip()
            if out:
                print(out)
            if err:
                print("[stderr]")
                print(err)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
