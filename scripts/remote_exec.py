from __future__ import annotations

import argparse
import getpass
import os

import paramiko


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one command on the mailbox server.")
    parser.add_argument("command")
    args = parser.parse_args()

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
        _, stdout, stderr = client.exec_command(args.command, timeout=180)
        status = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if out:
            print(out)
        if err:
            print("[stderr]")
            print(err)
        return status
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
