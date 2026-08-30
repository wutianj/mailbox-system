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
        "validate": "docker exec momo-link-caddy caddy validate --config /etc/caddy/Caddyfile",
        "restart": "docker restart momo-link-caddy",
        "health_http": "curl -fsS -H 'Host: mail.i7wap.xyz' http://103.214.172.30/health || true",
        "health_https": "curl -k -fsS --resolve mail.i7wap.xyz:443:103.214.172.30 https://mail.i7wap.xyz/health || true",
        "cert_files": "find /opt/momo-link/public/caddy-data/caddy/certificates -maxdepth 5 -type f | grep -i 'mail.i7wap.xyz' || true",
        "recent_mail_logs": "docker logs --tail 160 momo-link-caddy 2>&1 | grep -i -E 'mail.i7wap|challenge|certificate|tls.obtain|error' || true",
    }
    for name, command in commands.items():
        print(f"\n===== {name} =====")
        _, stdout, stderr = client.exec_command(command, timeout=180)
        status = stdout.channel.recv_exit_status()
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
        print(f"exit={status}")
finally:
    client.close()
