# Mailu Integration Plan

## Mode

Use Mailu as the mail platform and add a sidecar service named `mailbox-code-api`.

Mailu remains responsible for:

- SMTP ingress
- IMAP storage
- domain and mailbox ownership
- DKIM/DMARC/SPF mail-stack features
- admin users and built-in admin API

The sidecar is responsible for:

- batch mailbox generation for registration workflows
- per-mailbox API token binding
- `email----api_url` export
- code/link extraction
- high-concurrency polling API
- operational audit for code reads

## Local Source

```text
D:\yewu\mailbox-system\upstream\Mailu
```

## Integration Points

### Mailu Admin API

Use Mailu API and internal model patterns for:

- creating domains
- creating users/mailboxes
- creating authentication tokens
- listing users
- disabling users

Relevant code:

```text
upstream\Mailu\core\admin\mailu\api\v1\domain.py
upstream\Mailu\core\admin\mailu\api\v1\user.py
upstream\Mailu\core\admin\mailu\api\v1\token.py
upstream\Mailu\core\admin\mailu\models.py
```

### Mail Read Path

The sidecar should not let callers specify arbitrary mailbox access.

Read sequence:

```text
token -> mailbox_code_tokens row -> Mailu mailbox email -> IMAP/Maildir read
```

The URL email parameter is only checked against the bound mailbox.

## New Sidecar Tables

```text
mailbox_code_tokens
mailbox_code_reads
mailbox_batches
message_cache
```

## Batch Flow

```text
POST /admin/mailboxes/batch
  -> create Mailu users
  -> create sidecar mailbox token records
  -> return email/password/api_url rows
```

Export:

```text
email----https://mail.i7wap.xyz/mail/<token>/<urlencoded-email>
```

## Concurrency

- API is stateless and horizontally scalable.
- Redis caches latest message/code per mailbox.
- Worker pre-parses messages and fills `message_cache`.
- Direct IMAP fallback remains available when cache misses.
- Per-mailbox locks prevent repeated concurrent IMAP scans for the same mailbox.
