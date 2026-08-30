# Mailbox System

Production mailbox and code-receiving platform for `i7wap.xyz`.

The deployed system creates real mailbox accounts on the server and exports one
isolated API URL per mailbox:

```text
email----api_url
```

Each exported API token can read only its bound mailbox.

## Production

- Host: `103.214.172.30`
- Remote root: `/opt/mailbox-system`
- Public API: `https://mail.i7wap.xyz`
- Health: `https://mail.i7wap.xyz/health`

## Main Files

- `ARCHITECTURE.md`: system plan and rollout stages
- `docs/SOURCE-SELECTION.md`: mature upstream source selection
- `docs/OPERATIONS.md`: production operations and rollback commands
- `deploy/compose.yaml`: production Docker Compose stack
- `scripts/deploy_server.py`: Windows-to-server deployment script
- `ops/generate_mailboxes.py`: server-side batch mailbox generator
- `ops/export_mailboxes.py`: server-side active mailbox exporter

## Generate Mailboxes

Open the browser UI after logging in to the Mailu admin console:

```text
https://mail.i7wap.xyz/box
```

The page generates mailboxes into the browser first. You can then copy the
result or download a TXT file in `email----api_url` format. Browser-generated
mailboxes use random local parts without a fixed prefix by default.

Server-side command:

```bash
python3 /opt/mailbox-system/ops/generate_mailboxes.py --count 1000 --group batch-001
```

Exports are written to `/opt/mailbox-system/exports/`.
