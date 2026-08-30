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
        "caddy_logs": "docker logs --tail 260 momo-link-caddy 2>&1 | grep -i -E 'mail.i7wap|certificate|tls|acme|error|challenge|http-01' || true",
        "caddy_cert_files": "find /opt/momo-link/public/caddy-data/caddy -maxdepth 6 -type f | grep -i 'mail.i7wap\\|i7wap' | sed -n '1,120p'",
        "caddy_block": "awk '/mail.i7wap.xyz \\{/,/^\\}/ {print}' /opt/momo-link/public/Caddyfile",
        "caddy_ports": "docker exec momo-link-caddy sh -c 'ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || true'",
    }
    for name, command in commands.items():
        print(f"\n===== {name} =====")
        _, stdout, stderr = client.exec_command(command, timeout=120)
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
finally:
    client.close()
