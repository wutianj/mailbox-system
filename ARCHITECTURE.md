# Mailbox System Architecture

## Goal

Build a self-hosted mailbox platform on `103.214.172.30` with:

- real mailbox accounts
- per-mailbox access tokens
- one mailbox per API link
- export format: `email----api_url`
- multi-domain support
- domain changes without code changes

## Reuse Targets

- Primary upstream: `Mailu`
- Operational reference: `mailcow-dockerized`
- Admin reference: `Modoboa`
- Message API reference: `inbucket`
- Low-level fallback: `docker-mailserver`
- Docker-mailserver admin reference: `mailserver-admin`

See `docs/SOURCE-SELECTION.md`.

## High-Level Layout

```text
DNS
  i7wap.xyz MX -> mail.i7wap.xyz
  mail.i7wap.xyz A -> 103.214.172.30

Server
  Mailu mail stack         mail receive + real accounts + admin API
  mailbox-code-api         token-bound mailbox lookup
  admin extension          mailbox generation and export
  database                 domains, mailboxes, tokens, messages
```

## Core Rules

1. A mailbox is a real address, such as `abc123@i7wap.xyz`.
2. Every mailbox gets one token.
3. The API must resolve `token -> mailbox` before reading mail.
4. The email in the URL is only a consistency check.
5. Domain changes only update configuration/data, not code.

## Data Model

### domains

- id
- domain
- mx_host
- api_base_url
- status
- created_at

### mailboxes

- id
- domain_id
- local_part
- email
- mailbox_secret_hash
- api_token_hash
- status
- created_at
- expires_at

### messages

- id
- mailbox_id
- from_addr
- subject
- text_body
- html_body
- extracted_code
- extracted_link
- received_at

## API Shape

```text
POST /admin/domains
POST /admin/mailboxes/batch
GET  /admin/export?domain=i7wap.xyz

GET  /mail/<token>/<email>
GET  /mail/<token>/<email>/messages
GET  /api/mail/code?token=<token>&email=<email>
```

## Deployment Plan

### Phase 1

- inspect current server services and ports
- add docker-mailserver
- add a minimal API service
- generate and read two test mailboxes

### Phase 2

- add multi-domain support
- add batch generation
- add txt export
- add expiration and disable rules

### Phase 3

- switch MX for `i7wap.xyz`
- keep rollback records
- verify old path still recoverable

## Validation

- create mailbox A and B
- send test mail to A
- confirm A API can read A
- confirm A API cannot read B
- export `email----api_url` successfully
- change default domain and confirm old mailboxes still work
