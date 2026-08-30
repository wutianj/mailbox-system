# Mailbox System Operations

## Production

- Host: `103.214.172.30`
- Remote root: `/opt/mailbox-system`
- Public API: `https://mail.i7wap.xyz`
- Mail domain: `i7wap.xyz`
- Local workspace: `D:\yewu\mailbox-system`

Secrets are not stored in this repository. On the server they live in:

- `/opt/mailbox-system/mailbox-api.env`
- `/opt/mailbox-system/mailu.env`
- `/opt/mailbox-system/.env`

Local sensitive references are archived under:

- `D:\AccountSecrets\mailbox-system\`

## Service Layout

Mailu is the upstream mail platform. The custom sidecar service owns mailbox
batch generation, per-mailbox API tokens, export files, and message/code query
isolation.

Main containers:

- `mailbox-api`: FastAPI admin and public per-mailbox API
- `mailbox-worker`: IMAP collector and code/link parser
- `mailbox-db`: PostgreSQL metadata store
- `mailbox-redis`: API/worker Redis
- `mailbox-mailu-*`: Mailu SMTP/IMAP/admin/webmail/antispam stack

Public ports:

- `103.214.172.30:25`: SMTP inbound
- `103.214.172.30:80` and `103.214.172.30:443`: existing Caddy container

Local-only ports:

- `127.0.0.1:18080`: mailbox API
- `127.0.0.1:18088`: Mailu HTTP frontend

## Health Checks

From any machine:

```bash
curl -sS https://mail.i7wap.xyz/health
```

Expected result:

```json
{"ok":true,"mail_backend_enabled":true}
```

From the server:

```bash
cd /opt/mailbox-system
docker compose ps
docker logs --tail=100 mailbox-api
docker logs --tail=100 mailbox-worker
docker logs --tail=100 mailbox-mailu-smtp
```

## Generate Mailboxes

Browser workflow:

1. Log in to the Mailu admin console at `https://mail.i7wap.xyz/admin`.
2. Open `https://mail.i7wap.xyz/box` in the same browser.
3. Enter count, per-request batch size, and batch name.
4. Click `开始生成`.
5. Review the generated result in the page, then click `复制全部` or `下载 TXT`.

The browser result and downloaded `.txt` file use this format:

```text
email----api_url
```

Command-line workflow on the server:

```bash
python3 /opt/mailbox-system/ops/generate_mailboxes.py --count 1000 --group batch-001
```

The script creates real Mailu users and writes an export file under:

```text
/opt/mailbox-system/exports/
```

Server-side export format:

```text
email----api_url
```

Each `api_url` can read only that exact mailbox.

## Export Existing Active Mailboxes

Run on the server:

```bash
python3 /opt/mailbox-system/ops/export_mailboxes.py --domain i7wap.xyz --group batch-001
```

The export file is written under `/opt/mailbox-system/exports/`.

## Public Read API

Browser/API URL form:

```text
https://mail.i7wap.xyz/mail/{token}/{email}
```

Query API form:

```text
https://mail.i7wap.xyz/api/mail/code?token={token}&email={email}
```

The token is bound to one mailbox. A mismatched email returns `403`.

## Change Or Add Domains

1. Point DNS to this server:
   - `A mail.<domain> -> 103.214.172.30`
   - `MX <domain> -> mail.<domain>`
   - SPF includes this host
   - DKIM TXT from Mailu
   - DMARC TXT
2. Create or sync the domain with the admin API.
3. Generate mailboxes with the new domain:

```bash
python3 /opt/mailbox-system/ops/generate_mailboxes.py --domain example.com --count 1000 --group batch-001
```

4. Export active mailboxes:

```bash
python3 /opt/mailbox-system/ops/export_mailboxes.py --domain example.com --group batch-001
```

## Deploy Or Update

From the Windows workspace:

```powershell
py D:\yewu\mailbox-system\scripts\deploy_server.py
```

The deployment script uploads code, preserves existing server secrets, updates
Caddy, runs `docker compose up -d --build`, and performs a public smoke check.

## Rollback

The deploy script installs a rollback helper on the server:

```bash
/opt/mailbox-system/ROLLBACK.sh
```

Rollback restores the previous Caddyfile backup and restarts the mailbox stack
using the previous server-side files.

## DNS State For i7wap.xyz

Current intended state:

- `mail.i7wap.xyz A 103.214.172.30`
- `i7wap.xyz MX mail.i7wap.xyz`
- `dkim._domainkey.i7wap.xyz TXT` exists
- `_dmarc.i7wap.xyz TXT` exists
