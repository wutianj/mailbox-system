from __future__ import annotations

import getpass
import posixpath
from pathlib import Path

import paramiko


HOST = "103.214.172.30"
REMOTE_ROOT = "/opt/mailbox-system"
LOCAL_OPS = Path(__file__).resolve().parents[1] / "ops"


def main() -> int:
    password = getpass.getpass("SSH password: ")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=password, timeout=20, look_for_keys=False, allow_agent=False)
    sftp = client.open_sftp()
    try:
        mkdir_p(sftp, f"{REMOTE_ROOT}/ops")
        for path in LOCAL_OPS.glob("*.py"):
            sftp.put(str(path), f"{REMOTE_ROOT}/ops/{path.name}")
        _, stdout, stderr = client.exec_command(f"chmod 700 {REMOTE_ROOT}/ops/*.py && ls -l {REMOTE_ROOT}/ops", timeout=60)
        status = stdout.channel.recv_exit_status()
        print(stdout.read().decode(errors="replace").strip())
        err = stderr.read().decode(errors="replace").strip()
        if err:
            print("[stderr]\n" + err)
        return status
    finally:
        sftp.close()
        client.close()


def mkdir_p(sftp: paramiko.SFTPClient, path: str) -> None:
    parts = []
    while path not in ("", "/"):
        parts.append(path)
        path = posixpath.dirname(path)
    for part in reversed(parts):
        try:
            sftp.stat(part)
        except FileNotFoundError:
            sftp.mkdir(part)


if __name__ == "__main__":
    raise SystemExit(main())
