from __future__ import annotations

import getpass

import paramiko

from deploy_server import HOST, PORT, USER, run_smoke


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
        run_smoke(client, timeout=240)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
