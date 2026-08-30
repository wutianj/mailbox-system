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
    cmd = "docker network inspect $(docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null || true"
    _, stdout, stderr = client.exec_command(cmd, timeout=60)
    print(stdout.read().decode(errors="replace").strip())
    err = stderr.read().decode(errors="replace").strip()
    if err:
        print("[stderr]\n" + err)
finally:
    client.close()
